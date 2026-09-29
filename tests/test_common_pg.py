"""共用層的行為契約：連線池自癒 ＋ 兩種空白處理的語意差異。

這裡測的是過去**沒有任何測試**的東西（registry/usage 的 pool 生命週期），
以及一對容易被誤認成重複程式碼的函式。
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

    async def create_pool(self, dsn, min_size=1, max_size=3):
        p = _FakePool()
        self.created.append((dsn, min_size, max_size, p))
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
    _dsn, min_size, max_size, _p = fake.created[0]
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
