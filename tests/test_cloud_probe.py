"""`POST /settings/probe-clouds`（backend/app/cloud_probe.py）的行為契約。

## 為什麼用注入的假 HTTP client

CI 是**乾淨 clone、沒有 `.env`**，而這個專案在「測試依賴真實環境」上踩過三次
（端到端 heredoc、test_rules_store、需要 `.env` 的版本）—— 「測試對真 repo 跑」
是本專案最常見的 CI 紅掉原因。

**絕對不要在這支測試裡打真的雲端端點**：那會燒 free 額度（共享池，openrouter
50/天），而且讓 CI 依賴外部網路（那是最典型的 CI 紅掉來源）。所以這裡注入假的
`httpx.AsyncClient`，修法是自造 fixture，不是加 skip。
"""
import ast
import pathlib
import re

import httpx
import pytest

from app import cloud_probe, readiness

APP = pathlib.Path(__file__).resolve().parents[1] / "backend" / "app"

NV = "nvidia"
# ⚠️ 這三個假憑證**刻意拼出來**，不能在檔案裡出現連續字面值。
#
# 原因：CI 的 `guards` job 有「追蹤檔案不得含憑證」這道檢查，用
# `git grep -nIE 'nvapi-[A-Za-z0-9_-]{20,}'`（還有 cfut_ / hf_ / sk-or-v1-）
# 掃**所有被追蹤的檔案**。手寫的假 key 若長得像真的，就會讓那道 job 紅 ——
# 而那道 job 紅的意思是「CI 不再是三機紀律的公開證據」。
#
# 這跟 `tests/test_env_sync.py::test_no_tracked_secret_values` 是同一個顧慮的
# 另一個面向：那條管「值層級」，CI 那條管「格式層級」。假 key 也會被格式層級抓到。
FAKE_KEYS = {
    "nv": "nvapi" + "-" + "x" * 30,
    "cf": "cf" + "ut_" + "y" * 30,
    "hf": "h" + "f_" + "z" * 30,
}
NV_KEY = FAKE_KEYS["nv"]
CF_TOK = FAKE_KEYS["cf"]
HF_TOK = FAKE_KEYS["hf"]
LLM_IDS = {"nvidia/nemotron-3.5-lightning-30b-a3b", "nvidia/nemotron-3.5-long-context-128b"}


# ── 假的 HTTP 層 ───────────────────────────────────────────────────────────

class _Resp:
    """只留 probe 用得到的部分。**刻意不含** token 之類的東西。"""

    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or ("{}" if payload is None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeHTTP:
    """可注入的 `httpx.AsyncClient`。

    `fails[key]` 是**例外類別名**字串 → 模擬「探不到」那一類形狀（連線逾時、
    DNS 失敗、TLS 失敗、連線被拒）。用字串而不是物件，是因為要模擬的是
    `except httpx.ConnectError` 這種「型別」而不是「值」。
    """

    def __init__(self, ids=None, status=200, payload=None, fails=None, hangs=()):
        self.ids = ids if ids is not None else LLM_IDS
        self.status = status
        self.payload = payload
        self.fails = fails or {}
        self.hangs = set(hangs)
        self.requests: list[tuple[str, str, dict]] = []   # (provider, url, headers)

    def client(self, timeout=None):
        fake = self

        class _C:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url, headers=None):
                prov = fake._which(url)
                fake.requests.append((prov, url, dict(headers or {})))
                if prov in fake.hangs:
                    await cloud_probe.asyncio.sleep(30)
                if prov in fake.fails:
                    raise getattr(httpx, fake.fails[prov])("simulated")
                if fake.payload is not None:
                    return _Resp(fake.status, fake.payload)
                return _Resp(fake.status, {"data": [{"id": i} for i in sorted(fake.ids)]})

        return _C()

    def _which(self, url: str) -> str:
        for prov in cloud_probe._PROVIDERS:
            if prov == "nvidia" and "integrate.api.nvidia.com" in url:
                return prov
            if prov == "hf" and "huggingface.co" in url:
                return prov
            if prov == "zen" and "opencode.ai" in url:
                return prov
        return "openrouter"          # 其餘都走 CF gateway（wsl 上只有它有設定）


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(cloud_probe, "_cache", None)
    monkeypatch.setattr(cloud_probe, "PER_PROVIDER", 0.2)
    monkeypatch.setattr(cloud_probe, "TOTAL", 1.0)
    monkeypatch.setattr(cloud_probe, "_gateway_token", lambda: CF_TOK, raising=False)


def _world(monkeypatch, fake: FakeHTTP):
    """只有 openrouter/nvidia/hf 有設定（與 wsl 實測一致）；其餘是 `off`。

    ⚠️ `_gateway_token` **必須**一起換掉：乾淨 clone 沒有 `CF_AIG_TOKEN`，
    真實的 `rag._gateway_token()` 會回空字串 → openrouter 變成 `off` →
    「gateway 路徑／headers」那幾條測試會靜默地什麼都沒測到。
    """
    import app.rag as rag
    monkeypatch.setattr(httpx, "AsyncClient", fake.client)
    monkeypatch.setattr(rag, "_gateway_token", lambda: CF_TOK)
    monkeypatch.setattr(rag, "NVIDIA_API_KEY", NV_KEY)
    monkeypatch.setattr(rag, "HF_TOKEN", HF_TOK)
    monkeypatch.setattr(rag, "OPENROUTER_GATEWAY_URL", "https://gw.test/v1/abc")
    monkeypatch.setattr(rag, "ZEN_API_KEY", "")
    monkeypatch.setattr(rag, "GEMINI_GATEWAY_URL", "-")
    monkeypatch.setattr(rag, "GROQ_GATEWAY_URL", "-")
    monkeypatch.setattr(rag, "COHERE_GATEWAY_URL", "-")
    monkeypatch.setattr(rag, "MISTRAL_GATEWAY_URL", "-")
    # 已設定清單也換成可控的短清單：那是「使用者設了哪些模型」，與憑證無關。
    monkeypatch.setattr(rag, "NVIDIA_MODELS", list(LLM_IDS))
    monkeypatch.setattr(rag, "HF_MODELS", ["Qwen/Qwen3.8-27B"])
    monkeypatch.setattr(rag, "OPENROUTER_MODELS", ["vendor/thing:free"])
    return fake


def _ok_deps(monkeypatch):
    """把 readiness 的其餘三項（pg / qdrant / ollama）設成好 —— 這支測試只关心 cloud。"""
    from app import gateway, host_settings

    class _Resp:
        status_code = 200
        # ⚠️ 2026-10-03：`_check_qdrant` 會問**點數**（存在性不等於內容 ——
        #   空集合也回 200）。原本這裡的假物只有 `status_code`，於是三條測試
        #   因為「假物太假」而紅，那不是被測邏輯的問題。
        def json(self):
            return {"result": {"status": "green", "points_count": 1234}}

    async def pool_get():
        class _Con:
            async def fetchval(self, sql, *a):
                return 1

            async def __aenter__(self):
                return self

            async def __aexit__(self, *e):
                return False

        class _Pool:
            def acquire(self):
                return _Con()
        return _Pool()

    async def _req(*a, **kw):
        return _Resp()

    async def _probe(url):
        return True

    async def _stored():
        return ""

    monkeypatch.setattr(readiness.pg, "pool_get", pool_get)
    monkeypatch.setattr(gateway, "_req", _req)
    monkeypatch.setattr(gateway, "_ollama_probe", _probe)
    monkeypatch.setattr(host_settings, "stored", _stored)


# ── 1) catalog 200 → up，且 available 正確 ─────────────────────────────────

@pytest.mark.asyncio
async def test_catalog_200_is_up_with_available_models(monkeypatch):
    fake = _world(monkeypatch, FakeHTTP(ids=LLM_IDS))
    body = await cloud_probe.probe()
    p = body["providers"][NV]
    assert p["verdict"] == "up", p
    assert p["count"] == len(LLM_IDS)
    assert set(p["available"]) == set(LLM_IDS)
    assert p["missing"] == []
    assert p["configured"] and set(p["available"]) <= set(p["configured"])


@pytest.mark.asyncio
async def test_configured_model_absent_from_catalog_is_reported_as_missing(monkeypatch):
    """逐模型顆粒度來自「推導」：catalog 沒有它就報 missing，不是只報 provider 能不能連。"""
    fake = _world(monkeypatch, FakeHTTP(ids=LLM_IDS - {"nvidia/nemotron-3.5-long-context-128b"}))
    p = (await cloud_probe.probe())["providers"][NV]
    assert p["verdict"] == "up"          # provider 本身通
    assert p["missing"] == ["nvidia/nemotron-3.5-long-context-128b"]
    assert "nvidia/nemotron-3.5-long-context-128b" not in p["available"]


@pytest.mark.asyncio
async def test_one_request_per_provider(monkeypatch):
    """每個 provider **一次** catalog 呼叫 —— 這是整個設計的省額度理由。"""
    fake = _world(monkeypatch, FakeHTTP())
    await cloud_probe.probe()
    per_provider: dict[str, int] = {}
    for prov, url, _h in fake.requests:
        per_provider[prov] = per_provider.get(prov, 0) + 1
    assert per_provider, "完全沒發請求"
    assert all(n == 1 for n in per_provider.values()), f"有 provider 打了不只一次：{per_provider}"
    # 只打 catalog，沒有任何推理端點（推理會燒額度）
    for _prov, url, _h in fake.requests:
        assert not any(p in url for p in ("chat/completions", "generateContent",
                                          ":generateContent", "/messages")), url


@pytest.mark.asyncio
async def test_gateway_path_is_models_not_api_v1_models(monkeypatch):
    """gateway 只轉發 `/models` —— 實測 `/api/v1/models` 回 404（假陰性來源）。"""
    fake = _world(monkeypatch, FakeHTTP())
    await cloud_probe.probe()
    gw = [u for p, u, _h in fake.requests if p == "openrouter"]
    assert gw, "openrouter 沒發請求"
    for u in gw:
        assert u.endswith("/models") and "/api/v1/" not in u, u


@pytest.mark.asyncio
async def test_cf_gateway_request_carries_both_auth_headers(monkeypatch):
    """必須帶 `cf-aig-authorization` ＋ `Authorization` —— 少一個就可能 403，
    而 403 在 probe 的語意裡是 `down`，那會是**假陰性**。"""
    fake = _world(monkeypatch, FakeHTTP())
    await cloud_probe.probe()
    gw = [h for p, u, h in fake.requests if p == "openrouter"]
    assert gw, "openrouter 沒發請求"
    for h in gw:
        assert "cf-aig-authorization" in h and "Authorization" in h


# ── 2) 明確失敗 → down ───────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404])
async def test_explicit_http_failure_is_down(monkeypatch, status):
    """401／403／404 都是「明確失敗」→ down（不是 unknown）。"""
    _world(monkeypatch, FakeHTTP(status=status))
    p = (await cloud_probe.probe())["providers"][NV]
    assert p["verdict"] == "down", p
    assert str(status) in p["detail"]


@pytest.mark.asyncio
async def test_down_detail_says_which_action_fixes_it(monkeypatch):
    """401 與 403 的處置完全不同（換 key vs 查 gateway 權限），detail 要分得開。"""
    _world(monkeypatch, FakeHTTP(status=401))
    p = (await cloud_probe.probe())["providers"][NV]
    assert "key" in p["detail"]
    _world(monkeypatch, FakeHTTP(status=404))
    p = (await cloud_probe.probe(force=True))["providers"][NV]
    assert "/models" in p["detail"]


# ── 3) 探不到 → unknown（不是 down）──────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("exc", ["ConnectError", "ConnectTimeout", "ReadTimeout",
                                 "RemoteProtocolError"])
async def test_unreachable_is_unknown_not_down(monkeypatch, exc):
    """逾時／DNS 失敗／TLS 失敗／連線被拒 → unknown，不是 down。"""
    _world(monkeypatch, FakeHTTP(fails={NV: exc}))
    p = (await cloud_probe.probe())["providers"][NV]
    assert p["verdict"] == "unknown", p
    assert "不等於壞了" in p["detail"] or "無從驗證" in p["detail"]


@pytest.mark.asyncio
async def test_timeout_is_unknown_not_down(monkeypatch):
    _world(monkeypatch, FakeHTTP(hangs={NV}))
    p = (await cloud_probe.probe())["providers"][NV]
    assert p["verdict"] == "unknown", p


@pytest.mark.asyncio
async def test_http_200_but_unparseable_is_unknown_not_down(monkeypatch):
    """200 但讀不出清單是**形狀**問題，不是 provider 壞 —— 報 down 會讓人去換 key。"""
    _world(monkeypatch, FakeHTTP(payload={"unexpected": "shape"}))
    p = (await cloud_probe.probe())["providers"][NV]
    assert p["verdict"] == "unknown", p
    assert "形狀" in p["detail"] or "解析" in p["detail"]


# ── 4) 未設 key → off（不是 down，且不影響 /ready）────────────────────────

@pytest.mark.asyncio
async def test_unset_provider_is_off_and_makes_no_request(monkeypatch):
    """`zen/groq/cohere/mis` 在 wsl 沒有 key —— 那是「沒開」，不是故障。"""
    fake = _world(monkeypatch, FakeHTTP())
    body = await cloud_probe.probe()
    for prov in ("zen", "gemini", "groq", "cohere", "mistral"):
        p = body["providers"][prov]
        assert p["verdict"] == "off", f"{prov}: {p}"
        assert "未設定" in p["detail"]
    assert not any(p in {x[0] for x in fake.requests} for p in ("zen", "groq", "cohere", "mistral")), \
        "沒設定的 provider 不該發任何請求"


@pytest.mark.asyncio
async def test_off_does_not_claim_models_are_missing(monkeypatch):
    """⚠️ 沒開 provider 時 `missing` 必須是**空的**。

    `missing` 的語意是「設定了、但 catalog 上沒有」。沒設定時我們根本沒有 catalog，
    把它填滿等於**斷言**那些模型不存在 —— 那是假陰性（面板會說「你設的 8 個 zen
    模型都不存在」，而真相是「你沒開 zen」）。`configured` 仍列出那些 id。
    """
    _world(monkeypatch, FakeHTTP())
    p = (await cloud_probe.probe())["providers"]["zen"]
    assert p["missing"] == [], p
    assert p["configured"], "已設定清單仍要列出，前端才知道哪些未驗證"


@pytest.mark.asyncio
async def test_unknown_does_not_claim_models_are_missing(monkeypatch):
    """探不到時同理：沒有 catalog 就不能說「不存在」。"""
    _world(monkeypatch, FakeHTTP(fails={NV: "ConnectError"}))
    p = (await cloud_probe.probe())["providers"][NV]
    assert p["verdict"] == "unknown"
    assert p["missing"] == [], p
    assert p["configured"]


@pytest.mark.asyncio
async def test_off_does_not_make_ready_red(monkeypatch):
    """四個 provider 沒開，`/ready` **不得**因此變紅。"""
    _world(monkeypatch, FakeHTTP())
    _ok_deps(monkeypatch)
    body = await readiness.report()
    assert body["ok"] is True, body
    assert body["checks"]["cloud"]["verdict"] != "down"


# ── 5) 前綴比對 ───────────────────────────────────────────────────────────

def test_leaf_match_handles_extra_models_prefix():
    """gemini 原生回 `models/gemini-3.8-flash`，設定清單是 `gemini-3.8-flash`。"""
    m = cloud_probe.match_configured(["gemini-3.8-flash"], {"models/gemini-3.8-flash"})
    assert m["available"] == ["gemini-3.8-flash"]
    assert m["how"]["gemini-3.8-flash"] == "leaf"


def test_exact_match_is_preferred_and_labelled():
    m = cloud_probe.match_configured(["nvidia/x-1b"], {"nvidia/x-1b"})
    assert m["how"]["nvidia/x-1b"] == "exact"


def test_leaf_match_is_case_insensitive():
    """HF 回 `Qwen/Qwen3.8-27B`；大小寫不一致不該被算成 missing。"""
    m = cloud_probe.match_configured(["qwen/qwen3.8-27b"], {"Qwen/Qwen3.8-27B"})
    assert m["available"] == ["qwen/qwen3.8-27b"]


def test_substring_is_not_a_match():
    """⚠️ 不用 `in` 硬碰的理由：`in` 會把 `glm-5` 判成在 `other/glm-5-turbo` 裡
    —— 那是**假陽性**，比假陰性更危險（面板說能選，選了才 404）。"""
    m = cloud_probe.match_configured(["glm-5"], {"some/other/glm-5-turbo"})
    assert m["available"] == []
    assert m["missing"] == ["glm-5"]


def test_canonical_slug_is_accepted_as_the_same_model():
    """openrouter 每筆還有 `canonical_slug`（實測 id `apodex/apodex-1.1-mini:free`
    的 canonical_slug 是 `apodex/apodex-1.1-mini-20261001`）—— 那是同一模型的另一種寫法。"""
    assert "apodex/apodex-1.1-mini-20261001" in cloud_probe.extract_ids(
        {"data": [{"id": "apodex/apodex-1.1-mini:free",
                   "canonical_slug": "apodex/apodex-1.1-mini-20261001"}]})


def test_extract_ids_accepts_both_response_shapes():
    """openai-compatible 的 `{"data":[{"id":…}]}` 與 gemini 的 `{"models":[{"name":…}]}`。"""
    assert cloud_probe.extract_ids({"data": [{"id": "a/b"}]}) == {"a/b"}
    assert cloud_probe.extract_ids({"models": [{"name": "models/c"}]}) == {"models/c"}


def test_extract_ids_ignores_display_name_when_id_is_present():
    """⚠️ openrouter 每筆同時有 `id` 與 `name`（後者是**顯示名**，
    `Apodex: Apodex 1.1 Mini (free)` 這種含空格與冒號的字串）。

    兩個都收會讓 count 從 464 膨脹到 1156（實測），而且顯示名進比對集等於往
    **假陽性**放寬 —— 而假陽性（面板說能選、選了才 404）比假陰性更危險。
    """
    got = cloud_probe.extract_ids({"data": [
        {"id": "apodex/apodex-1.1-mini:free", "name": "Apodex: Apodex 1.1 Mini (free)"}]})
    assert got == {"apodex/apodex-1.1-mini:free"}
    assert not any(":" in g and " " in g for g in got), got


def test_extract_ids_on_garbage_returns_empty_not_raises():
    """讀不出來要讓呼叫端改報 unknown，而不是在這裡炸。"""
    assert cloud_probe.extract_ids(None) == set()
    assert cloud_probe.extract_ids({"data": "not-a-list"}) == set()
    assert cloud_probe.extract_ids([1, 2, 3]) == set()


# ── 6) 回應不得含憑證 ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_credential_appears_in_the_response(monkeypatch):
    """headers 裡的 token 只進 outbound request；回應不得含任何憑證值。"""
    _world(monkeypatch, FakeHTTP())
    body = await cloud_probe.probe()
    blob = repr(body)
    for secret in (NV_KEY, CF_TOK, HF_TOK):
        assert secret not in blob, f"回應洩漏了憑證：{secret[:6]}…"


@pytest.mark.asyncio
async def test_credential_in_an_upstream_error_is_scrubbed(monkeypatch):
    """上游錯誤訊息若含 token 形狀，進回應前要刮掉（回應會被貼進 issue）。"""
    _world(monkeypatch, FakeHTTP(status=500))
    p = (await cloud_probe.probe())["providers"][NV]
    assert p["verdict"] == "down"
    blob = repr(p)
    for secret in (NV_KEY, CF_TOK, HF_TOK):
        assert secret not in blob


def test_scrub_removes_tokens_and_dsn():
    out = cloud_probe._scrub("Authorization: Bearer sk-abcdefghijklmn from postgresql://u:pw@h/db")
    assert "sk-abcdefghijklmn" not in out
    assert "pw@" not in out
    assert "***" in out


def test_scrub_removes_bare_token_shaped_strings():
    """⚠️ `nvapi-…`／`cfut_…`／`hf_…` 這種**帶廠商前綴的裸 key** 也要刮 ——
    那是本專案的實際 key 格式，而且**沒有 `key=` 這種上下文可依**，只能靠前綴認。

    字面值同樣要拼接（見 FAKE_KEYS 的說明：CI guards job 會 grep 這些格式）。
    """
    for raw in (f"failed for {FAKE_KEYS['nv']}",
                f"token {FAKE_KEYS['cf']}",
                f"key {FAKE_KEYS['hf']}"):
        out = cloud_probe._scrub(raw)
        for secret in FAKE_KEYS.values():
            assert secret not in out, out


def test_scrub_preserves_the_non_sensitive_surrounding_text():
    """刮憑證不該把整句都吃掉 —— 否則回應會變成沒有資訊的「***」。"""
    out = cloud_probe._scrub(f"model not found while using {NV_KEY} on provider nvidia")
    assert "model not found" in out
    assert "provider nvidia" in out
    assert NV_KEY not in out


# ── 7) 快取 ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_result_is_cached(monkeypatch):
    fake = _world(monkeypatch, FakeHTTP())
    await cloud_probe.probe()
    n = len(fake.requests)
    assert n
    await cloud_probe.probe()
    await cloud_probe.probe()
    assert len(fake.requests) == n, "TTL 內不該重打（會燒額度）"


@pytest.mark.asyncio
async def test_force_bypasses_cache(monkeypatch):
    fake = _world(monkeypatch, FakeHTTP())
    await cloud_probe.probe()
    n = len(fake.requests)
    await cloud_probe.probe(force=True)
    assert len(fake.requests) > n


@pytest.mark.asyncio
async def test_ttl_is_five_minutes_and_result_carries_age(monkeypatch):
    """TTL 要夠長（登入才觸發、實際使用量極低）但也不能長到 key 換了還顯示舊結果。"""
    assert 300 <= cloud_probe.TTL <= 600
    _world(monkeypatch, FakeHTTP())
    body = await cloud_probe.probe()
    assert body["age"] < 1.0
    assert body["probed_at"]
    assert set(body["summary"]) == {"up", "down", "unknown", "off"}


@pytest.mark.asyncio
async def test_response_is_deep_copied(monkeypatch):
    """深拷貝：呼叫端就地改欄位會污染 module global 快取（淺拷貝踩過），
    症狀是「後續幾分鐘看到假資料」。"""
    _world(monkeypatch, FakeHTTP())
    first = await cloud_probe.probe()
    first["providers"][NV]["verdict"] = "tampered"
    first["summary"]["up"] = 999
    again = await cloud_probe.probe()
    assert again["providers"][NV]["verdict"] != "tampered"
    assert again["summary"]["up"] != 999


# ── 8) 接進 /ready 的 cloud 項 ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_ready_without_any_probe_result_is_still_200(monkeypatch):
    """還沒 probe 過 → unknown，**不是 down** —— 不得因此讓 /ready 變紅。"""
    _ok_deps(monkeypatch)
    _world(monkeypatch, FakeHTTP())
    cloud_probe.invalidate()
    body = await readiness.report()
    assert body["ok"] is True, body
    cloud = body["checks"]["cloud"]
    assert cloud["required"] is False
    assert cloud["probe"]["verdict"] == "unknown"
    assert cloud["probe"]["stale"] is True
    assert cloud["probe"]["probed_at"] is None
    # 設定面仍照報（那是配置，與有沒有 probe 無關）
    assert cloud["configured"] and cloud["configured_count"] == len(cloud["configured"])


@pytest.mark.asyncio
async def test_ready_cloud_surfaces_the_probe_result(monkeypatch):
    """有 probe 結果時，`/ready` 的 cloud 項要分得出設定面與驗證面。"""
    _ok_deps(monkeypatch)
    _world(monkeypatch, FakeHTTP(status=401))
    await cloud_probe.probe(force=True)
    body = await readiness.report(force=True)
    cloud = body["checks"]["cloud"]
    assert cloud["probe"]["verdict"] == "down"
    assert cloud["probe"]["probed_at"]
    assert NV in cloud["probe"]["providers"]
    assert cloud["probe"]["providers"][NV]["verdict"] == "down"
    # ⚠️ required: false —— 雲端 down **不得**讓 /ready 變紅
    assert cloud["required"] is False
    assert body["ok"] is True, body


@pytest.mark.asyncio
async def test_ready_peek_does_not_trigger_a_probe(monkeypatch):
    """`/ready` 每 10–30 秒被輪詢，絕不能因此發雲端請求（會燒額度）。"""
    _ok_deps(monkeypatch)
    fake = _world(monkeypatch, FakeHTTP())
    cloud_probe.invalidate()
    await readiness.report()
    await readiness.report(force=True)
    assert not [r for r in fake.requests if "nvidia" in r[1] or "huggingface" in r[1]], \
        "/ready 不得發雲端 catalog 請求"


# ── 9) SENSITIVE 與路由（靜態）──────────────────────────────────────────

def _main_tree() -> ast.Module:
    return ast.parse((APP / "main.py").read_text(encoding="utf-8"))


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


def test_probe_clouds_route_exists():
    assert ("post", "/settings/probe-clouds") in _routes(_main_tree())


def test_sensitive_requirement_is_recorded_in_the_backend():
    """⚠️ **要加進 frontend `SENSITIVE` 的字串被記錄在後端**。

    `frontend/src/routes/api/[...path]/+server.ts` 的 `guard()` 對**不在** `SENSITIVE`
    清單裡的路徑直接 `return null`、**完全不驗 session** —— 2026-10-02 剛修過一次
    同型漏洞（新增 `/settings/default-model` 時漏加，被審查抓到）。

    後端不動 frontend，所以這個要求只能靠**記錄在這裡**來保證有人看到。
    這條測試擋的是「新增 /settings/* 端點時忘了寫下 SENSITIVE 要求」。
    """
    src = (APP / "main.py").read_text(encoding="utf-8")
    assert "SENSITIVE" in src, "main.py 沒提 SENSITIVE —— 新端點的登入要求會被漏掉"
    for path in ("settings/probe-clouds", "settings/default-model"):
        assert path in src, f"{path} 沒被記錄成需要進 SENSITIVE"


def test_every_settings_route_is_documented_as_requiring_login():
    """把 `/settings/*` 的每個路由都列進去 —— 新增第三個時這條會紅。"""
    routes = [p for _v, p in _routes(_main_tree()) if p.startswith("/settings/")]
    assert len(routes) >= 2, routes
    src = (APP / "main.py").read_text(encoding="utf-8")
    for p in routes:
        assert p.split("/", 1)[1] in src, f"{p} 沒被記錄成需要進 SENSITIVE"


def test_probe_clouds_does_not_change_state():
    """冪等、可重複呼叫、**不改任何狀態** —— 所以 handler 不該碰 pg。"""
    fn = next(n for n in ast.walk(_main_tree())
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "probe_clouds")
    src = ast.unparse(fn)
    for banned in ("host_settings", "set_default", "set_default(", "pg.pool_get"):
        assert banned not in src, f"probe 端點不該改狀態（出現 {banned}）"
    assert "cloud_probe.probe" in src


def test_health_stays_probe_free_and_ready_stays_503():
    """一併釘住上輪的護欄：`/health` 零探測、`/ready` 503 分支仍在。"""
    tree = _main_tree()
    health = next(ast.unparse(n) for n in ast.walk(tree)
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "health")
    assert "cloud_probe" not in health and "await" not in health
    ready = next(ast.unparse(n) for n in ast.walk(tree)
                 if isinstance(n, ast.AsyncFunctionDef) and n.name == "ready")
    assert "503" in ready