"""per-host 預設聊天模型（backend/app/host_settings.py）的行為契約。

## 為什麼全部用假的 pg

CI 是**乾淨 clone、沒有 `.env`**，而這個專案在「測試依賴真實環境」上踩過三次
（commit 記錄：端到端 heredoc、test_rules_store、真 .env 的版本）。所以這支
測試**不碰**真實 Postgres／`.env`／qdrant／ollama：一個能認得本模組那三句 SQL
的假池就夠了（`FakeDB`）。修法是自造 fixture，不是加 skip。

## 這裡釘住的三件事

1. **設定→清除→`effective` 回退 `LLM_MODEL`**，以及有值時 `effective` 等於它。
2. **DB 不可用時降級**，而且查詢路徑不因此失敗。
3. **舊的 `backends` 行不受影響** —— 尤其 `llm` 那一列不能被這個功能改到。
   第 3 點有兩條測試：SQL 層（`host_settings` 的 SQL 不含 `backends`、
   心跳的 SQL 不含 `host_settings`）與原始碼層（`registry.py` 完全不知道
   `host_settings` 存在）—— 後者擋的是「日後有人為了少一張表而把它塞回
   `backends.llm`」這種重構。
"""
import ast
import pathlib
import re

import pytest

from app import gateway, host_settings, registry
from app.common import pg

APP = pathlib.Path(__file__).resolve().parents[1] / "backend" / "app"

BACKENDS_INSERT_RE = re.compile(r"INSERT INTO backends\s*\(([^)]*)\)", re.S)


# ── 假 pg：認得 host_settings 的三句 ＋ registry 的 backends 兩句 ──────────

class FakeDB:
    """最小可用的假 asyncpg。記錄每一句 SQL（驗證「有沒有碰到別的表」）。

    刻意**不用**真的 Postgres：乾淨 clone 的 CI 沒有，也沒有 `.env` 的 DSN。
    """

    def __init__(self):
        self.settings: dict[str, str] = {}      # host_id → default_model
        self.backends: dict[str, dict] = {}      # host_id → 那一列
        self.sql: list[str] = []                 # 依序記錄
        self.fail = False                        # True = 連線失敗
        self.table_exists = True

    # ── asyncpg 介面的一小塊 ──
    async def execute(self, sql, *args):
        self.sql.append(sql)
        if self.fail:
            raise RuntimeError("連不上 postgres（測試）")
        if "CREATE TABLE" in sql and not self.table_exists:
            raise RuntimeError('relation "host_settings" does not exist')
        head = sql.strip().upper()
        if head.startswith("INSERT INTO BACKENDS"):
            cols = [c.strip() for c in BACKENDS_INSERT_RE.search(sql).group(1).split(",")]
            row = dict(zip(cols, args))
            self.backends[row["host_id"]] = row
            return "INSERT 0 1"
        if head.startswith("INSERT INTO HOST_SETTINGS"):
            self.settings[args[0]] = args[1]
            return "INSERT 0 1"
        if head.startswith("DELETE FROM HOST_SETTINGS"):
            self.settings.pop(args[0], None)
            return "DELETE 1"
        if head.startswith("DELETE FROM BACKENDS"):
            self.backends.pop(args[0], None)
            return "DELETE 1"
        return "OK"

    async def fetchrow(self, sql, *args):
        await self.execute(sql, *args)
        if "host_settings" not in sql:
            return None
        v = self.settings.get(args[0])
        return {"default_model": v} if v is not None else None

    # ── 測試用的觀察點 ──
    def selects(self):
        return [s for s in self.sql if s.strip().upper().startswith("SELECT")]


class FakePool:
    def __init__(self, db):
        self.db = db

    def acquire(self):
        db = self.db

        class _Acq:
            async def __aenter__(self):
                return db

            async def __aexit__(self, *exc):
                return False
        return _Acq()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """每個測試都從「沒快取、假的 pg、假的主機代號」開始。

    monkeypatch `registry.HOST_ID` 而不是設環境變數：HOST_ID 是在 import 時
    讀進模組變數的，而 `host_settings` 是**每次呼叫時**讀 `registry.HOST_ID`
    —— 所以測試可以改它，真實環境（compose 的 `HOST_ID`）則完全不受影響。

    快取用 monkeypatch 歸零（不是 `invalidate()`）：`invalidate()` 是刻意
    **保留值**的（見 host_settings.py），所以它不保證「乾淨」。
    """
    monkeypatch.setattr(host_settings, "_cache", None)
    monkeypatch.setattr(registry, "HOST_ID", "unit-test-host")
    yield


def _use(monkeypatch, db: FakeDB | None = None):
    """把 host_settings 與 registry 的 pg 存取都接到假池。"""
    db = db or FakeDB()

    async def pool_get():
        return FakePool(db)

    monkeypatch.setattr(pg, "pool_get", pool_get)
    return db


# ── 設定 / 清除 / effective ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_then_clear_falls_back_to_llm_model(monkeypatch):
    """設定 → 讀回來；清除 → `model` 變 null、`effective` 回到 LLM_MODEL。"""
    db = _use(monkeypatch)

    assert await host_settings.set_default("qwen2.5-coder:latest") == "qwen2.5-coder:latest"
    assert db.settings == {"unit-test-host": "qwen2.5-coder:latest"}
    got = host_settings.envelope(await host_settings.stored())
    assert got["model"] == "qwen2.5-coder:latest"
    assert got["effective"] == "qwen2.5-coder:latest"

    assert await host_settings.set_default(None) == ""
    assert db.settings == {}, "清除應該刪掉那一列，而不是留一個空字串"
    cleared = host_settings.envelope(await host_settings.stored())
    assert cleared["model"] is None
    assert cleared["effective"] == gateway.LLM_MODEL


@pytest.mark.asyncio
async def test_empty_string_also_clears(monkeypatch):
    """前端送 `""` 與送 `null` 意思相同（契約明訂），別只擋其中一種。"""
    db = _use(monkeypatch)
    await host_settings.set_default("some-model")
    await host_settings.set_default("")
    assert db.settings == {}
    assert host_settings.envelope(await host_settings.stored())["effective"] == gateway.LLM_MODEL


def test_normalize_strips_surrounding_whitespace():
    assert host_settings.normalize("  qwen3:8b\n") == "qwen3:8b"
    assert host_settings.normalize("   ") == ""
    assert host_settings.normalize(None) == ""


@pytest.mark.asyncio
async def test_value_written_is_what_gets_read_back(monkeypatch):
    """存進去的字串要原樣讀回來（不轉大小寫、不剝 ollama/ 前綴）。"""
    _use(monkeypatch)
    for name in ("qwen2.5-coder:latest", "Qwen3:14B", "openrouter/qwen/qwen3.8-27b:free"):
        await host_settings.set_default(name)
        assert (await host_settings.stored()) == name


@pytest.mark.asyncio
async def test_each_host_has_its_own_row(monkeypatch):
    """表以 host_id 當主鍵：兩台機器的設定不該互相蓋掉。"""
    db = _use(monkeypatch)
    await host_settings.set_default("mine")
    # 換一台機器：同一張表、不同的 host_id
    monkeypatch.setattr(registry, "HOST_ID", "other-host")
    host_settings.invalidate()
    assert await host_settings.stored() == "", "別台機器的設定不該出現在這台"
    db.settings["other-host"] = "theirs"
    host_settings.invalidate()
    assert await host_settings.stored() == "theirs"
    assert db.settings["unit-test-host"] == "mine"


# ── 回應形狀（前端契約）────────────────────────────────────────────────────

def test_envelope_shape_has_exactly_four_keys():
    """前端契約固定四個欄位；多一個或少一個都是破壞契約。

    特別是**沒有 `host`** —— 這個端點只設定自己那台，跨機寫入不在範圍。
    """
    got = host_settings.envelope("m")
    assert set(got) == {"ok", "host", "model", "effective"}
    assert got["ok"] is True
    assert got["host"] == registry.HOST_ID


def test_envelope_reports_unset_as_null_not_empty_string():
    got = host_settings.envelope("")
    assert got["model"] is None, "未設定要回 null（前端據此顯示「未設定」），不是空字串"
    assert got["effective"] == gateway.LLM_MODEL
    assert got["effective"], "effective 永不為空：LLM_MODEL 一定有值"


def test_envelope_effective_never_null_even_if_llm_model_blank(monkeypatch):
    """LLM_MODEL 被設成空字串時 effective 也不該是 null（前端要顯示字串）。"""
    monkeypatch.setattr(gateway, "LLM_MODEL", "")
    assert host_settings.envelope("")["effective"] == ""


# ── 快取 ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reads_are_cached_within_ttl(monkeypatch):
    """查詢路徑不該每個 query 都打一次 DB。"""
    db = _use(monkeypatch)
    for _ in range(5):
        await host_settings.stored()
    assert len(db.selects()) == 1, f"5 次讀不該是 5 次 SELECT（實際 {len(db.selects())}）"


@pytest.mark.asyncio
async def test_write_takes_effect_immediately_without_rereading(monkeypatch):
    """PUT 之後下一個查詢就用新值 —— 不能還在用 PUT 前的快取。"""
    db = _use(monkeypatch)
    assert await host_settings.stored() == ""
    before = len(db.selects())
    await host_settings.set_default("fresh")
    assert await host_settings.stored() == "fresh"
    assert len(db.selects()) == before, "寫入已更新快取，不需要再讀一次"


@pytest.mark.asyncio
async def test_ttl_expiry_rereads(monkeypatch):
    """TTL 過期要回 DB（別的行程／多個 worker 改掉設定時才看得到）。"""
    db = _use(monkeypatch)
    await host_settings.stored()
    db.settings["unit-test-host"] = "changed-elsewhere"
    assert await host_settings.stored() == "", "TTL 內應該還是舊值"
    monkeypatch.setattr(host_settings, "TTL", 0.0)   # 讓它立刻過期
    assert await host_settings.stored() == "changed-elsewhere"


@pytest.mark.asyncio
async def test_unset_is_cached_as_unset_not_as_missing(monkeypatch):
    """未設定也要快取 —— 否則「沒有設定」會變成每個 query 都打 DB。"""
    db = _use(monkeypatch)
    for _ in range(3):
        assert await host_settings.stored() == ""
    assert len(db.selects()) == 1


@pytest.mark.asyncio
async def test_invalidate_forces_reread(monkeypatch):
    db = _use(monkeypatch)
    await host_settings.stored()
    db.settings["unit-test-host"] = "x"
    host_settings.invalidate()
    assert await host_settings.stored() == "x"


# ── 降級 ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_db_failure_falls_back_to_llm_model(monkeypatch):
    """pg 掛掉時 `effective` 回到 LLM_MODEL，而不是讓設定表變成故障點。"""
    db = _use(monkeypatch)
    db.fail = True
    got = host_settings.envelope(await host_settings.stored())
    assert got["model"] is None
    assert got["effective"] == gateway.LLM_MODEL


@pytest.mark.asyncio
async def test_failure_backoff_is_shorter_than_normal_ttl(monkeypatch):
    """失敗結果的快取必須比正常 TTL 短：否則 pg 回來後還要等 10 秒。

    這條擋的是把兩個 TTL 寫成同一個常數 —— 症狀是「pg 回來了但設定還是不生效」。
    """
    assert host_settings.TTL_FAIL < host_settings.TTL
    db = _use(monkeypatch)
    db.fail = True
    await host_settings.stored()
    n = len(db.sql)
    await host_settings.stored()
    assert len(db.sql) == n, "失敗也該有退避，不該每個查詢都去戳一個掛掉的 pg"
    db.fail = False
    db.settings["unit-test-host"] = "back"
    monkeypatch.setattr(host_settings, "TTL_FAIL", 0.0)  # 退避期結束
    assert await host_settings.stored() == "back"


@pytest.mark.asyncio
async def test_last_known_value_survives_a_db_outage(monkeypatch):
    """pg 抖一下時，使用者剛剛設好的模型不該無聲消失。

    走 `invalidate()`（保留值、強制重讀）而不是直接把值塞進快取 ——
    這條要驗的是「讀不到時降級到**上次已知的值**」，不是「有沒有快取」。
    """
    db = _use(monkeypatch)
    await host_settings.set_default("chosen")
    db.fail = True
    host_settings.invalidate()
    assert await host_settings.stored() == "chosen"


@pytest.mark.asyncio
async def test_outage_with_no_previous_value_falls_back_to_llm_model(monkeypatch):
    """對照上一條：從來沒讀成功過時，沒有「上次已知值」，只能回 LLM_MODEL。"""
    db = _use(monkeypatch)
    db.fail = True
    assert await host_settings.stored() == ""
    assert host_settings.envelope(await host_settings.stored())["effective"] == gateway.LLM_MODEL


@pytest.mark.asyncio
async def test_resolve_never_raises(monkeypatch):
    """查詢路徑：即使 stored() 整個爆掉也要回「未指定」，不能讓 /query 失敗。"""
    _use(monkeypatch)

    async def boom():
        raise RuntimeError("連不上 postgres")

    monkeypatch.setattr(host_settings, "stored", boom)
    assert await host_settings.resolve("") == ""


@pytest.mark.asyncio
async def test_requested_model_wins_over_stored_default(monkeypatch):
    """/query 指定的 model 優先於存的設定（前端選單優先，不要被設定蓋掉）。"""
    _use(monkeypatch)
    await host_settings.set_default("stored-default")
    assert await host_settings.resolve("explicit/model") == "explicit/model"


@pytest.mark.asyncio
async def test_resolve_returns_empty_string_not_llm_model_when_unset(monkeypatch):
    """未設定時回 ""，讓 rag.generate() 走 gateway._llm_model_for(選中的 ollama)。

    回 `LLM_MODEL` 會**改掉**既有行為：多台 ollama 配 `OLLAMA_MODELS` 時，
    `LLM_MODEL` 不等於該台對應的模型（見 host_settings.py docstring）。
    """
    _use(monkeypatch)
    assert await host_settings.resolve("") == ""
    assert gateway.LLM_MODEL != ""  # 前置條件：真的有人在用 LLM_MODEL


# ── backends 不受這個功能影響 ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_host_settings_sql_never_touches_backends(monkeypatch):
    db = _use(monkeypatch)
    await host_settings.set_default("m")
    await host_settings.stored()
    assert db.sql, "測試自己沒跑到 SQL，fixture 可能壞了"
    offenders = [s for s in db.sql if "backends" in s.lower()]
    assert not offenders, f"預設模型的功能不該寫 backends 表：{offenders}"


@pytest.mark.asyncio
async def test_heartbeat_still_writes_backends_and_leaves_settings_alone(monkeypatch):
    """心跳照樣寫 `backends`（含 `llm`），但完全不碰 host_settings。

    反過來證明這個功能沒有把設定藏進 `backends.llm` 那一列 ——
    那裡每 30 秒就被 `llm=EXCLUDED.llm` 覆寫一次。
    """
    db = _use(monkeypatch)
    monkeypatch.setattr(registry, "_ips", lambda: [])
    monkeypatch.setattr(registry, "_system_id", lambda: "mid")
    monkeypatch.setattr(registry, "_my_hostname", lambda: "unit-test")

    await registry.heartbeat(["m:1"], "llm-from-heartbeat")
    row = db.backends["unit-test-host"]
    assert row["llm"] == "llm-from-heartbeat"
    before = dict(row)

    await host_settings.set_default("per-host-default")
    assert await host_settings.stored() == "per-host-default"

    assert db.backends["unit-test-host"] == before, "設定預設模型不該改動 backends 的任何一列"
    assert db.settings == {"unit-test-host": "per-host-default"}
    hb = [s for s in db.sql if "backends" in s.lower()]
    assert hb and all("host_settings" not in s for s in hb), \
        "心跳的 SQL 不得提到 host_settings（否則下一個版本就會互相覆寫）"


def test_registry_source_never_mentions_host_settings():
    """原始碼層的釘子：擋「為了少一張表而把 default_model 塞回 backends.llm」。

    `registry.py` 不知道 `host_settings` 存在，所以「心跳不會碰到它」是
    結構上成立的，而不是靠記得 UPDATE 不要帶某一列。
    """
    src = (APP / "registry.py").read_text(encoding="utf-8")
    assert "host_settings" not in src, "registry.py 不該知道 host_settings 的存在"


# ── 靜態檢查 main.py（不 import fastapi —— 根 venv 沒有安裝）──────────────

def _main_tree() -> ast.Module:
    return ast.parse((APP / "main.py").read_text(encoding="utf-8"))


def _routes(tree) -> dict[tuple[str, str], ast.AsyncFunctionDef | ast.FunctionDef]:
    """(method, path) → 該 endpoint 的函式本體。刻意用 AST（見 conftest 的說明）。"""
    out: dict[tuple[str, str], ast.AsyncFunctionDef | ast.FunctionDef] = {}
    for node in tree.body:
        # ⚠️ 必須同時收 FunctionDef 與 AsyncFunctionDef：FastAPI 的 endpoint
        #    幾乎都是 async，只收其中一種會讓這支測試「靜默找到 0 個 route」
        #    —— 而「找不到」在沒有 assert 訊息時看起來跟「通過」差不多。
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for d in node.decorator_list:
            if not isinstance(d, ast.Call):
                continue
            verb = getattr(d.func, "attr", "") or getattr(d.func, "id", "")
            if verb.lower() in ("get", "put", "post") and d.args:
                out[(verb.lower(), d.args[0].value)] = node
    return out


def test_both_methods_exist_on_the_settings_route():
    routes = _routes(_main_tree())
    assert routes, "static route parser 抓到 0 個 endpoint —— 這個測試本身壞了"
    for verb in ("get", "put"):
        assert (verb, "/settings/default-model") in routes, \
            f"/settings/default-model 少了 {verb.upper()}（前端契約要求兩個都有）"


def test_put_body_has_no_host_field():
    """PUT 的 body 只有 `model` —— 加上 `host` 就變成跨機寫入了。"""
    tree = _main_tree()
    fields = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            fields[node.name] = [t.target.id for t in node.body
                                 if isinstance(t, ast.AnnAssign) and isinstance(t.target, ast.Name)]
    body_models = [n for n, f in fields.items() if "model" in f]
    assert body_models, "找不到含 model 欄位的請求模型（PUT 的 body）"
    for n in body_models:
        assert "host" not in fields[n], "PUT 的 body 不得有 host 欄位：這個端點只設定自己這台"


def test_query_resolves_the_default_model_before_calling_answer():
    """/query 必須把 resolve() 的結果傳給 rag.answer(model=…)。

    只斷言「main.py 有提到 resolve」不夠 —— 那可能是註解裡的字。所以這裡看的是
    `query` 函式本體裡 **`rag.answer(...)` 那一個呼叫的 `model=` 關鍵字**：
    它的值必須是 `await host_settings.resolve(...)`。少了那個關鍵字，
    `/query` 的 model 指定會被丟掉（等於這條接線不存在）。
    """
    fn = _routes(_main_tree())[("post", "/query")]
    model_args = [kw.value for node in ast.walk(fn)
                  if isinstance(node, ast.Call) and ast.unparse(node.func) == "rag.answer"
                  for kw in node.keywords if kw.arg == "model"]
    assert model_args, "rag.answer 沒有收到 model= —— /query 的模型選擇接線不見了"
    src = ast.unparse(model_args[0])
    assert "host_settings.resolve" in src, \
        f"model= 收到的是 {src!r}，不是 resolve() 的結果 —— 存的預設模型不會生效"
    assert "q.model" in src, f"model= 必須保留 /query 指定的模型（它的優先權高於預設）"


def test_host_settings_adds_no_environment_variable():
    """刻意不加環境變數（TTL 是模組常數）。

    多一個 `os.getenv` 就多一份「程式讀得到、容器拿不到」的風險
    （backend/DESIGN.md〈陷阱〉），而且必須同時補 compose.yaml 與 .env.example。
    這條測試擋下「順手加個 knob」—— 真要加的話，先刪掉這條測試並更新
    .env.example。
    """
    tree = ast.parse((APP / "host_settings.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "getenv"):
            raise AssertionError(
                f"host_settings.py:{(node.lineno)} 用了 os.getenv —— "
                "新增變數必須同時補 compose.yaml 與 .env.example，"
                "而且「程式讀得到」不等於「容器拿得到」（見 backend/DESIGN.md）")