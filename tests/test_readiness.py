"""`GET /ready`（backend/app/readiness.py）的行為契約。

## 為什麼全部用假的探測函式

CI 是**乾淨 clone、沒有 `.env`**，而這個專案在「測試依賴真實環境」上踩過三次
（端到端 heredoc、test_rules_store、需要 `.env` 的版本）—— 「測試對真 repo 跑」
是本專案最常見的 CI 紅掉原因。所以這支測試**不碰**真 Postgres／qdrant／ollama：
`World` 把四個依賴的可觀察行為做成可注入的假身。修法是自造 fixture，不是加 skip。

## 這裡釘住的三件事（與需求一一對應）

1. 全部好 → 200（`ok: true`）；某個必要依賴壞 → 503（`ok: false` 且該項 `ok:false`）。
2. **探測逾時 → `verdict: unknown`（「無從驗證」），不是 `down`（「壞了」）**。
3. `default_model` 指向不存在的模型 → ollama 那項失敗**並指名是哪個模型**；
   雲端閘道未設定 → **不**算故障。

外加兩條護欄：`/health` 不得長出探測（語意凍結），`/ready` 的 503 與 `/health`
的無條件 200 不得被調換。
"""
import ast
import pathlib

import pytest

from app import gateway, host_settings, rag, readiness, registry

APP = pathlib.Path(__file__).resolve().parents[1] / "backend" / "app"

EMBED = gateway.EMBED_MODEL          # 乾淨 clone 沒有 .env 時是原始碼預設 bge-m3:latest
HOST = "http://ollama-under-test:11434"


# ── 假的四個依賴 ───────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class _Acq:
    def __init__(self, con):
        self.con = con

    async def __aenter__(self):
        return self.con

    async def __aexit__(self, *exc):
        return False


class _Pool:
    def __init__(self, con):
        self.con = con

    def acquire(self):
        return _Acq(self.con)


class _Con:
    def __init__(self, world):
        self.w = world

    async def fetchval(self, sql, *a):
        self.w.calls.append(("fetchval", sql))
        if self.w.pg_hang:
            await readiness.asyncio.sleep(30)
        if self.w.pg_raises:
            raise self.w.pg_raises
        return self.w.pg_value


class World:
    """一次 /ready 探測會碰到的四個依賴的可控假身。

    每個開關的語意都對應真實世界的形狀，特別是 ollama 那一個：**TCP 通 ≠ 可用**
    （模型不齊時 `/api/embed` 與 `/api/generate` 都是 404，不是連不上）。
    """

    def __init__(self):
        self.calls: list[tuple] = []
        # postgres
        self.pg_value = 1
        self.pg_raises: Exception | None = None
        self.pg_hang = False
        # qdrant
        self.qdrant_status = 200
        self.qdrant_raises: Exception | None = None
        # ollama
        self.tags = {EMBED, gateway.LLM_MODEL}
        self.tcp = True
        self.probe_ok: bool | None = None      # None = 讓 tags 決定
        self.tags_raises: Exception | None = None
        # host_settings（default_model）
        self.default_model = ""
        # 雲端 provider（預設全部未設定 → 不是故障）
        self.cloud = {}

    def install(self, monkeypatch):
        w = self

        async def pool_get():
            w.calls.append(("pool_get",))
            if w.pg_hang:
                await readiness.asyncio.sleep(30)
            if w.pg_raises:
                raise w.pg_raises
            return _Pool(_Con(w))

        async def _req(kind, candidates, method, path, **kw):
            w.calls.append((kind, path))
            if w.qdrant_raises:
                raise w.qdrant_raises
            return _Resp(w.qdrant_status)

        async def _tcp_open(url, timeout=2.0):
            w.calls.append(("tcp_open", url))
            return w.tcp

        async def _ollama_tags(url):
            w.calls.append(("tags", url))
            if w.tags_raises:
                raise w.tags_raises
            return set(w.tags)

        async def _ollama_probe(url):
            w.calls.append(("probe", url))
            if w.probe_ok is not None:
                return w.probe_ok
            return {EMBED, gateway._llm_model_for(url)} <= set(w.tags)

        async def stored():
            return w.default_model

        def token():          # rag._gateway_token 是同步的（讀 env 或 token 檔）
            return ""

        monkeypatch.setattr(readiness.pg, "pool_get", pool_get)
        monkeypatch.setattr(gateway, "_req", _req)
        monkeypatch.setattr(gateway, "_tcp_open", _tcp_open)
        monkeypatch.setattr(gateway, "_ollama_tags", _ollama_tags)
        monkeypatch.setattr(gateway, "_ollama_probe", _ollama_probe)
        monkeypatch.setattr(gateway, "OLLAMA_URLS", [HOST])
        monkeypatch.setattr(host_settings, "stored", stored)
        monkeypatch.setattr(rag, "_gateway_token", token)
        for name in ("OPENROUTER_GATEWAY_URL", "ZEN_API_KEY", "NVIDIA_API_KEY",
                     "GEMINI_GATEWAY_URL", "GROQ_GATEWAY_URL", "COHERE_GATEWAY_URL",
                     "MISTRAL_GATEWAY_URL"):
            monkeypatch.setattr(rag, name, "")
        monkeypatch.setattr(rag, "HF_BASE_URL", "")
        monkeypatch.setattr(rag, "HF_TOKEN", "")
        monkeypatch.setattr(registry, "HOST_ID", "unit-test-host")
        return w


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """每個測試都從「沒有快取」開始（結果快取是 module global，會跨測試污染）。"""
    monkeypatch.setattr(readiness, "_cache", None)
    monkeypatch.setattr(readiness, "PER_CHECK", 0.2)   # 逾時測試要快
    monkeypatch.setattr(readiness, "TOTAL", 1.0)


def _ready(monkeypatch, **kw):
    """裝好假身並回傳它（要改哪個依賴的形狀就直接 `w.pg_raises = …`）。"""
    return World().install(monkeypatch)


# ── 1) 全部好 → 200 ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_all_good_is_ok(monkeypatch):
    _ready(monkeypatch)
    body = await readiness.report()
    assert body["ok"] is True, body
    assert set(body["checks"]) == {"postgres", "qdrant", "ollama", "cloud"}
    for name, c in body["checks"].items():
        assert c["verdict"] == "up", f"{name} 應為 up：{c}"
    assert body["host"] == registry.HOST_ID


@pytest.mark.asyncio
async def test_report_carries_the_model_in_use(monkeypatch):
    """`default_model`＝**實際會用的**那個（存的，沒設就用 LLM_MODEL），
    而 `stored` 另外標出網頁上設的那個（可能 null）。"""
    w = _ready(monkeypatch)
    w.default_model = ""
    body = await readiness.report()
    assert body["default_model"] == gateway.LLM_MODEL
    assert body["stored"] is None

    w.default_model = "some-model:1b"
    body = await readiness.report(force=True)
    assert (body["default_model"], body["stored"]) == ("some-model:1b", "some-model:1b")


@pytest.mark.asyncio
async def test_cloud_not_configured_is_not_a_failure(monkeypatch):
    """沒有開雲端 provider 是正常狀況，不是故障 —— 不該讓 503。"""
    w = _ready(monkeypatch)
    body = await readiness.report()
    assert body["checks"]["cloud"]["ok"] is True
    assert body["checks"]["cloud"]["required"] is False
    assert body["checks"]["cloud"]["configured"] == []
    assert body["ok"] is True


@pytest.mark.asyncio
async def test_cloud_reports_configured_providers_without_calling_them(monkeypatch):
    """已設定的雲端 provider 只報狀態、**不發請求**（輪詢會燒 free 額度）。"""
    w = _ready(monkeypatch)
    monkeypatch.setattr(rag, "ZEN_API_KEY", "fake-key-for-test")
    body = await readiness.report()
    c = body["checks"]["cloud"]
    assert c["configured"] == ["zen"]
    assert c["ok"] is True and c["required"] is False
    assert not any("zen" in str(call) for call in w.calls), "雲端檢查不得發任何請求"


# ── 2) 必要依賴壞 → 503 ───────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("world_attr,check", [
    ("pg_raises", "postgres"),
    ("qdrant_raises", "qdrant"),
])
async def test_required_dependency_down_makes_it_not_ready(monkeypatch, world_attr, check):
    w = _ready(monkeypatch)
    setattr(w, world_attr, RuntimeError("boom"))
    body = await readiness.report()
    assert body["ok"] is False
    assert body["checks"][check]["ok"] is False
    assert body["checks"][check]["verdict"] == "down"
    assert body["checks"][check]["required"] is True
    # 其他項目不受牽連（要能分辨是哪一個壞）
    others = [n for n in body["checks"] if n != check]
    assert all(body["checks"][n]["ok"] for n in others)


@pytest.mark.asyncio
async def test_qdrant_missing_collection_is_down_and_names_it(monkeypatch):
    """只探 `/` 會回 200 卻沒有 collection —— 那種「探測通過但查詢全滅」抓不到。"""
    w = _ready(monkeypatch)
    w.qdrant_status = 404
    body = await readiness.report()
    assert body["checks"]["qdrant"]["verdict"] == "down"
    assert "不存在" in body["checks"]["qdrant"]["detail"]
    assert gateway.QDRANT_URLS[0] not in body["checks"]["qdrant"]["detail"]  # 沒洩漏位址


@pytest.mark.asyncio
async def test_ollama_unreachable_is_down(monkeypatch):
    w = _ready(monkeypatch)
    w.tcp = False
    w.probe_ok = False
    body = await readiness.report()
    assert body["ok"] is False
    assert body["checks"]["ollama"]["verdict"] == "down"
    assert "連不上" in body["checks"]["ollama"]["detail"]


# ── 3) 「無從驗證」≠「壞了」 ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_probe_timeout_is_unknown_not_down(monkeypatch):
    """探測逾時是「無從驗證」。把它報成 `down` 會讓排查被帶去錯的方向。

    這條對本專案特別重要：2026-10-02 的 `/query` 500 與「ollama 壞了」無關，
    誤報成依賴故障正是那種誤導。
    """
    w = _ready(monkeypatch)
    w.pg_hang = True
    body = await readiness.report()
    pg = body["checks"]["postgres"]
    assert pg["verdict"] == "unknown", pg
    assert "無從驗證" in pg["detail"]
    assert pg["ok"] is False          # 不能驗證就不能承諾 → 不 ready
    assert body["ok"] is False
    # 而其他項目照常被探（不必等 hung 的那一項）
    assert body["checks"]["ollama"]["verdict"] == "up"


@pytest.mark.asyncio
async def test_unexpected_probe_result_is_unknown(monkeypatch):
    """回應看不懂（SELECT 1 回的不是 1）也歸 unknown，不是 down。"""
    w = _ready(monkeypatch)
    w.pg_value = None
    body = await readiness.report()
    assert body["checks"]["postgres"]["verdict"] == "unknown"
    assert "無從驗證" in body["checks"]["postgres"]["detail"]


@pytest.mark.asyncio
async def test_tags_unreadable_is_down_because_the_host_is_reachable(monkeypatch):
    """TCP 通但 /api/tags 讀不到 → `down`（不是 unknown）：主機活著，是它有問題。"""
    w = _ready(monkeypatch)
    w.probe_ok = False
    w.tags_raises = RuntimeError("500 Internal Server Error")
    body = await readiness.report()
    assert body["checks"]["ollama"]["verdict"] == "down"
    assert "/api/tags" in body["checks"]["ollama"]["detail"]


# ── 4) default_model 納入探測 ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_default_model_missing_on_that_host_fails_and_names_the_model(monkeypatch):
    """`default_model` 指向該機沒有的模型 → ollama 那項 down 並指名模型。

    症狀是 `/api/generate` 吃 **404**（不是連不上），所以只探 TCP 抓不到。
    """
    w = _ready(monkeypatch)
    w.default_model = "ghost-model:99b"
    body = await readiness.report()
    o = body["checks"]["ollama"]
    assert o["verdict"] == "down"
    assert "ghost-model:99b" in o["detail"]
    assert EMBED not in o["detail"].split("（")[0], "缺的是聊天模型，不是嵌入模型"


@pytest.mark.asyncio
async def test_default_model_present_is_ok(monkeypatch):
    w = _ready(monkeypatch)
    w.default_model = "ghost-model:99b"
    w.tags.add("ghost-model:99b")
    body = await readiness.report()
    assert body["checks"]["ollama"]["verdict"] == "up"
    assert body["ok"] is True


@pytest.mark.asyncio
async def test_default_model_overrides_llm_model_for_that_host(monkeypatch):
    """該台沒有 `_llm_model_for`、但有 `default_model` → 仍算 ready。

    這是「預設模型」這個功能的核心：此時查詢實際會用 `default_model`，
    而 `gateway._ollama_probe` 內建檢查的是 `_llm_model_for(url)`。直接沿用它
    會在這裡**誤報 not ready**（症狀：明明查得了，/ready 說不行）。
    """
    w = _ready(monkeypatch)
    w.tags = {EMBED, "ghost-model:99b"}      # 刻意沒有 gateway._llm_model_for
    w.default_model = "ghost-model:99b"
    body = await readiness.report()
    assert body["checks"]["ollama"]["verdict"] == "up", body["checks"]["ollama"]


@pytest.mark.asyncio
async def test_cloud_default_model_is_not_looked_for_in_ollama(monkeypatch):
    """`default_model` 是雲端模型時，不該去 ollama 的 /api/tags 裡找它。"""
    w = _ready(monkeypatch)
    w.default_model = "openrouter/qwen/qwen3.8-27b:free"
    w.tags = {EMBED}                        # 沒有 _llm_model_for → 真的不可用
    body = await readiness.report()
    o = body["checks"]["ollama"]
    assert o["verdict"] == "down"
    assert "openrouter/" not in o["detail"], o["detail"]


@pytest.mark.asyncio
async def test_embed_model_missing_is_reported_separately(monkeypatch):
    """嵌入模型缺了 → 每個查詢都會卡在 /api/embed，detail 要指名它。"""
    w = _ready(monkeypatch)
    w.tags = {gateway.LLM_MODEL}
    body = await readiness.report()
    o = body["checks"]["ollama"]
    assert o["verdict"] == "down"
    assert EMBED in o["detail"]


# ── 5) 快取 ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_result_is_cached(monkeypatch):
    """/ready 會被輪詢，不該每次都打三個相依服務。"""
    w = _ready(monkeypatch)
    await readiness.report()
    n = len(w.calls)
    assert n, "第一次應該真的探了"
    await readiness.report()
    await readiness.report()
    assert len(w.calls) == n, "TTL 內不該再探"


@pytest.mark.asyncio
async def test_force_bypasses_the_cache(monkeypatch):
    """/ready?force=1：剛修好的人不必等 TTL 才知道自己修好了。"""
    w = _ready(monkeypatch)
    await readiness.report()
    n = len(w.calls)
    await readiness.report(force=True)
    assert len(w.calls) > n


@pytest.mark.asyncio
async def test_cache_entry_carries_its_age(monkeypatch):
    _ready(monkeypatch)
    first = await readiness.report()
    assert first["age"] < 1.0
    monkeypatch.setattr(readiness, "TTL", 0.0)      # 立刻過期
    await readiness.report()
    fresh = await readiness.report()
    assert fresh["age"] < 1.0


@pytest.mark.asyncio
async def test_cached_body_is_not_mutated_by_callers(monkeypatch):
    """回應是淺拷貝：呼叫端就地改它不會污染快取（否則第二次呼叫看到假資料）。"""
    _ready(monkeypatch)
    first = await readiness.report()
    first["checks"]["ollama"]["ok"] = "tampered"
    first["ok"] = "tampered"
    again = await readiness.report()
    assert again["ok"] is True
    assert again["checks"]["ollama"]["ok"] is True


# ── 6) 不洩漏憑證 ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dsn_credentials_are_scrubbed_from_the_response(monkeypatch):
    """pg 的錯誤字串可能帶 DSN；/ready 的 body 會被面板與監控抓走。"""
    w = _ready(monkeypatch)
    w.pg_raises = RuntimeError(
        "connection to server at postgres failed: postgresql://rag:sup3rsecret@postgres:5432")
    body = await readiness.report()
    blob = repr(body)
    assert "sup3rsecret" not in blob, blob
    assert "***" in body["checks"]["postgres"]["detail"]


# ── 7) /health 語意凍結（靜態）──────────────────────────────────────────

def _main_tree() -> ast.Module:
    return ast.parse((APP / "main.py").read_text(encoding="utf-8"))


def _fn(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"main.py 找不到 {name}()")


def _routes(tree):
    out = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for d in node.decorator_list:
            if isinstance(d, ast.Call):
                verb = getattr(d.func, "attr", "") or getattr(d.func, "id", "")
                if verb.lower() in ("get", "put", "post") and d.args:
                    out[(verb.lower(), d.args[0].value)] = node.name
    return out


def test_health_must_stay_probe_free():
    """`/health` 是 liveness：永遠 200、零探測。

    它被 `frontend/src/routes/api/[...path]/+server.ts` 的同儕面板與
    `scripts/wait-stack.sh` 依賴。若它在某個依賴壞時回 503，面板就會說
    「連線失敗」，而後端其實好好地活著 —— 那是這個專案反覆在修的那類錯誤。
    """
    src = ast.unparse(_fn(_main_tree(), "health"))
    for banned in ("readiness", "_ollama_probe", "_host_probe_log", "pool_get",
                   "_req", "await"):
        assert banned not in src, f"/health 不得探測依賴（出現 {banned!r}）"


def test_health_route_has_no_conditional_status():
    """`/health` 不能出現依賴狀態而改變回傳碼的路徑（JSONResponse 就不該有）。"""
    src = ast.unparse(_fn(_main_tree(), "health"))
    assert "JSONResponse" not in src and "status_code" not in src


def test_ready_route_returns_503_when_not_ready():
    """/ready 的 503 必須存在（呼叫端不必解析 body 就能判斷）。"""
    fn = _fn(_main_tree(), "ready")
    codes = {kw.value.value for node in ast.walk(fn)
             if isinstance(node, ast.Call)
             for kw in node.keywords if kw.arg == "status_code"
             and isinstance(kw.value, ast.Constant)}
    assert 503 in codes, f"/ready 沒有 503 分支（看到 {codes}）"
    assert ("get", "/ready") in _routes(_main_tree())


# ── 8) CLOUD_MODEL_PREFIXES 不漂移 ──────────────────────────────────────

def test_cloud_prefixes_match_generate_routing():
    """`rag.generate()` 加了 provider 卻忘了加進 CLOUD_MODEL_PREFIXES → pytest 紅。

    忘了的後果是靜默的：readiness 會拿 `openrouter/…` 去 ollama 的 /api/tags 裡找，
    然後報「該機沒有 openrouter/…」—— 一個永遠修不好的誤導。
    """
    tree = ast.parse((APP / "rag.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "generate")
    used = {node.args[0].value for node in ast.walk(fn)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute) and node.func.attr == "startswith"
            and node.args and isinstance(node.args[0], ast.Constant)}
    assert used == set(rag.CLOUD_MODEL_PREFIXES), (
        f"generate() 的前綴 {sorted(used)} 與 CLOUD_MODEL_PREFIXES "
        f"{sorted(rag.CLOUD_MODEL_PREFIXES)} 不一致")