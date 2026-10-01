"""共用層的行為契約：連線池自癒 ＋ 兩種空白處理的語意差異 ＋ 連線逾時。

這裡測的是過去**沒有任何測試**的東西（registry/usage 的 pool 生命週期），
以及一對容易被誤認成重複程式碼的函式。

2026-09-30（mbp 回報）追加「連線逾時」一節：mbp 的 `POSTGRES_DSN` 指向離線的
x570 時 `/query` +60s、`/hosts` 掛死 >20s。根因是 `asyncpg.create_pool` **自己
沒有** `timeout` 參數 —— connect 的逾時要經 `connect_kwargs` 轉給
`asyncpg.connect()`，兩層簽名都不顯眼。細節見該節的說明。
"""
import pytest

from app.common import pg
from app.common import text


class _FakePool:
    def __init__(self):
        self.closed = 0

    async def close(self):
        self.closed += 1


class _FakeAsyncpg:
    def __init__(self):
        self.created = []

    async def create_pool(self, dsn, min_size=1, max_size=3, **kw):
        p = _FakePool()
        # 保留 kwargs：逾時那節要斷言 connect_kwargs 真的送進來
        self.created.append((dsn, min_size, max_size, p, kw))
        return p


@pytest.fixture(autouse=True)
def _reset_pool():
    """每個測試前後都把 module global 的池歸零，避免互相污染。"""
    pg._pool = None
    yield
    pg._pool = None


# --- 連線池：建立/重用/丟棄 -------------------------------------------------

@pytest.mark.asyncio
async def test_pool_created_once_then_reused(monkeypatch):
    fake = _FakeAsyncpg()
    monkeypatch.setattr(pg, "asyncpg", fake)
    a = await pg.pool_get()
    b = await pg.pool_get()
    assert a is b, "第二次呼叫應重用同一個池，不該再開一個"
    assert len(fake.created) == 1


@pytest.mark.asyncio
async def test_pool_sized_for_concurrent_heartbeat(monkeypatch):
    """原本 registry=3、usage=2；合併後取 3。這是刻意選的，別被改回 2。"""
    fake = _FakeAsyncpg()
    monkeypatch.setattr(pg, "asyncpg", fake)
    await pg.pool_get()
    _dsn, min_size, max_size, _p, _kw = fake.created[0]
    assert (min_size, max_size) == (1, 3)


@pytest.mark.asyncio
async def test_drop_pool_closes_and_clears(monkeypatch):
    fake = _FakeAsyncpg()
    monkeypatch.setattr(pg, "asyncpg", fake)
    p = await pg.pool_get()
    await pg.drop_pool()
    assert p.closed == 1
    assert pg._pool is None
    # 丟掉後要能重建（否則 PG 重啟就永遠回不來）
    p2 = await pg.pool_get()
    assert p2 is not p


@pytest.mark.asyncio
async def test_drop_pool_is_safe_when_idle_or_close_fails(monkeypatch):
    await pg.drop_pool()  # 沒有池時不能拋
    broken = _FakePool()
    broken.close = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    pg._pool = broken
    await pg.drop_pool()  # close() 失敗也要吞掉，並且清掉 global
    assert pg._pool is None


@pytest.mark.asyncio
async def test_pool_get_raises_when_asyncpg_missing(monkeypatch):
    monkeypatch.setattr(pg, "asyncpg", None)
    with pytest.raises(RuntimeError, match="asyncpg"):
        await pg.pool_get()


# --- 這是本次修掉的 bug ------------------------------------------------------

@pytest.mark.asyncio
async def test_usage_track_drops_pool_on_failure(monkeypatch):
    """usage 過去失敗時只 swallow 例外、不關池 → PG 重啟後永遠不自癒。

    這是迴歸測試：拿掉 drop_pool() 這行就會失敗。
    """
    from app import usage

    async def boom():
        raise RuntimeError("PG 連不到")

    dropped = []
    monkeypatch.setattr(pg, "pool_get", boom)

    async def record():
        dropped.append(True)

    monkeypatch.setattr(pg, "drop_pool", record)

    await usage.track("openai", "gpt-x", 10)  # 不應拋出
    assert dropped, "track() 失敗後必須 drop_pool，否則會抱著死池不放"


@pytest.mark.asyncio
async def test_usage_snapshot_drops_pool_on_failure(monkeypatch):
    from app import usage

    async def boom():
        raise RuntimeError("PG 連不到")

    dropped = []
    monkeypatch.setattr(pg, "pool_get", boom)

    async def record():
        dropped.append(True)

    monkeypatch.setattr(pg, "drop_pool", record)

    assert await usage.snapshot() == []  # 失敗回空表，不拋
    assert dropped


@pytest.mark.asyncio
async def test_registry_heartbeat_drops_pool_on_failure(monkeypatch):
    from app import registry

    async def boom():
        raise RuntimeError("PG 連不到")

    dropped = []
    monkeypatch.setattr(pg, "pool_get", boom)

    async def record():
        dropped.append(True)

    monkeypatch.setattr(pg, "drop_pool", record)

    with pytest.raises(RuntimeError, match="heartbeat failed"):
        await registry.heartbeat(["m"], "ollama")
    assert dropped


# --- 兩個空白函式語意不同（別再合併成一個）----------------------------------

def test_collapse_ws_keeps_single_space():
    assert text.collapse_ws("  民法　第1條  ") == "民法 第1條"


def test_squash_removes_all_whitespace():
    assert text.squash("  民法　第1條  ") == "民法第1條"


def test_the_two_disagree_which_is_the_point():
    """兩個函式在有空白時結果不同 —— 這正是不能合併的證據。"""
    raw = "契約 解除"
    assert text.collapse_ws(raw) != text.squash(raw)
    # 比對用途（squash）才不受空格影響
    assert text.squash("契約 解除") == text.squash("契約解除")


def test_squash_tolerates_none():
    assert text.squash(None) == ""


# --- 連線逾時（2026-09-30 mbp 實測）-----------------------------------

@pytest.mark.asyncio
async def test_pool_passes_connect_timeout_directly():
    """逾時必須**直給** `timeout=`，不能包成 `connect_kwargs={"timeout": …}`。

    為什麼要這樣斷言（2026-10-01 實測踩到）：
    `create_pool` 沒有「名叫 timeout」的參數，但它有 `**connect_kwargs`，會**整包
    轉給 `connect()`**。所以舊寫法 `connect_kwargs={"timeout": 3}` 會被轉成
    `connect(..., connect_kwargs={"timeout": 3})` → TypeError，pool 永遠建不起來，
    症狀是 `/hosts` 永遠空、log 寫 `heartbeat skipped`。舊測試只斷言「kwargs 裡有
    connect_kwargs」，用 fake 檢查**形狀**而不是 **API**，於是一路綠。

    底下那條 `test_pool_kwargs_are_accepted_by_real_asyncpg` 才是能擋住這類 bug 的；
    這條保留是為了把「逾時值有送出、且不是 60s」講清楚（60 就是 2026-09-30
    mbp 掛 60s 的那個值）。
    """
    fake = _FakeAsyncpg()
    pg._pool = None
    orig, pg.asyncpg = pg.asyncpg, fake
    try:
        await pg.pool_get()
    finally:
        pg.asyncpg = orig
        pg._pool = None
    _dsn, _ms, _mx, _p, kw = fake.created[0]
    assert kw.get("timeout") == pg.PG_CONNECT_TIMEOUT, (
        f"逾時沒直給 timeout，而是 {kw} —— asyncpg 會把 connect_kwargs 整包轉給 connect()"
    )
    # 釘住「不是 60」：60 就是 2026-09-30 mbp 掛 60s 的那個值
    assert kw["timeout"] < 60
    assert "connect_kwargs" not in kw, (
        "connect_kwargs 會被原樣轉給 connect()，而 connect() 沒有這個參數 → TypeError"
    )


@pytest.mark.asyncio
async def test_pool_kwargs_are_accepted_by_real_asyncpg():
    """**用真的 asyncpg 簽名**驗我們送出去的每個 kwarg，不靠 fake。

    為什麼不能用 `inspect.signature(create_pool).bind(...)` 就好：create_pool 的
    VAR_KEYWORD 叫 `connect_kwargs`，它**接受任何字串**當關鍵字參數名 ——
    `connect_kwargs={"timeout":3}` 在 bind 時完全通過，落到 `connect()` 才炸。
    真正該問的對象是 `asyncpg.connect`（它沒有 VAR_KEYWORD，多的名字會直接 TypeError）。
    """
    import inspect

    import asyncpg as real_asyncpg

    fake = _FakeAsyncpg()
    pg._pool = None
    orig, pg.asyncpg = pg.asyncpg, fake
    try:
        await pg.pool_get()
    finally:
        pg.asyncpg = orig
        pg._pool = None

    connect_params = inspect.signature(real_asyncpg.connect).parameters
    pool_own = {"min_size", "max_size", "loop", "connection_class",
                "record_class", "setup", "init", "reset", "max_inactive_connection_lifetime",
                "max_queries", "max_cached_statement_lifetime", "max_cacheable_statement_lifetime",
                "server_settings", "command_timeout", "statement_cache_size",
                "max_inactive_transaction_lifetime", "ssl", "connection_timeout"}
    _dsn, _ms, _mx, _p, kw = fake.created[0]
    forwarded = {k: v for k, v in kw.items() if k not in pool_own}
    assert forwarded, "這條測試需要至少一個會被轉給 connect() 的 kwarg 才有意義"
    unknown = set(forwarded) - set(connect_params)
    assert not unknown, (
        f"{sorted(unknown)} 不是 asyncpg.connect 的參數 → create_pool 會把它們"
        f"原樣轉給 connect()，而 connect() 沒有 VAR_KEYWORD，執行期直接 TypeError。"
        f"（送出的是 {kw}）"
    )


def _reload_pg(monkeypatch):
    """重讀 pg 模組讓常數重算，並在測試結束後還原。

    不用「刪 sys.modules」：那對已被 package `__init__` 釘住的屬性無效
    （踩過：`test_connect_timeout_env_override` 因此拿到舊值 3.0 而失敗）。
    `importlib.reload` 才真的重跑模組層程式碼。
    """
    import importlib
    from app.common import pg

    monkeypatch.delenv("PG_CONNECT_TIMEOUT", raising=False)
    reloaded = importlib.reload(pg)
    return reloaded, monkeypatch


def test_connect_timeout_default_is_three_seconds(monkeypatch):
    """預設 3 秒（可由 PG_CONNECT_TIMEOUT 覆蓋）。

    3 秒的根據：這裡只連**本機 compose 的 pg 容器**（2026-09-30 各台 /hosts 已
    改讀自己的 pg），正常是毫秒級。3 秒足夠容忍容器剛起來的慢啟動，又不會讓
    一個離線位址把整個 request 拖掉。
    """
    reloaded, mp = _reload_pg(monkeypatch)
    import importlib
    try:
        assert reloaded.PG_CONNECT_TIMEOUT == 3.0
    finally:
        mp.undo()
        importlib.reload(reloaded)


def test_connect_timeout_env_override(monkeypatch):
    """`PG_CONNECT_TIMEOUT=7.5` 要真的生效。

    這條抓得到一個真陷阱：`monkeypatch.delenv` 在同一個 module 物件上無效 ——
    刪 `sys.modules` 只會讓下一次 import 重跑，而 `app.common.pg` 已經被
    `app.common` 這個 package 的 `__init__` 屬性釘住。所以這裡改用
    `importlib.reload`，並在最後還原（否則後面的測試會拿到 7.5）。
    """
    import importlib
    from app.common import pg

    monkeypatch.setenv("PG_CONNECT_TIMEOUT", "7.5")
    reloaded = importlib.reload(pg)
    try:
        assert reloaded.PG_CONNECT_TIMEOUT == 7.5
    finally:
        monkeypatch.undo()
        importlib.reload(pg)


def test_empty_env_value_does_not_produce_zero_timeout(monkeypatch):
    """空值不能變成 0 —— 0 在 asyncpg 意為「不設逾時」，等於回到 60s 病根。

    這是本專案反覆出現的一類 bug（2026-09-29 一次過修掉 25 處
    `os.getenv(K, D)` 空值陷阱），所以釘住。
    """
    monkeypatch.setenv("PG_CONNECT_TIMEOUT", "")
    import importlib
    from app.common import pg

    reloaded = importlib.reload(pg)
    try:
        assert reloaded.PG_CONNECT_TIMEOUT == 3.0
    finally:
        monkeypatch.undo()
        importlib.reload(pg)
