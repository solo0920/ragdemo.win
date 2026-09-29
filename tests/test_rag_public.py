"""rag.py 的 public 介面：先前完全沒有測試覆蓋的那 11 個函式。

這些都是「別人會直接呼叫、但改壞了不會有人發現」的層級 —— 條號抽取、主機標籤、
ops 狀態機、keep_alive 型別轉換。全部不碰網路（`_req` / `embed` / `_pick` 都被
換掉），符合 pre-push「純函式不需外部服務」的限制。
"""
import json

import pytest

from app import cn_parse, rag, gateway, law_meta, retrieve


class FakeResp:
    """夠用的 httpx 回應替身。"""

    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


# ── extract_article_no：檢索的命脈 ────────────────────────────────────────
# 這一組的輸出格式是「比對鍵」：會拿去和 Qdrant payload 的 article_no 做
# 去空白後的完全比對（見 _exact_match）。所以格式改動等於換掉比對基準。

def test_extract_article_no_arabic():
    assert cn_parse.extract_article_no("第20條") == "第 20 條"
    assert cn_parse.extract_article_no("第 20 條") == "第 20 條"
    assert cn_parse.extract_article_no("契約第259條違約金") == "第 259 條"


def test_extract_article_no_chinese_numerals():
    assert cn_parse.extract_article_no("第十條") == "第 10 條"
    assert cn_parse.extract_article_no("第二十條") == "第 20 條"
    assert cn_parse.extract_article_no("第一百零五條") == "第 105 條"
    assert cn_parse.extract_article_no("第兩百條") == "第 200 條"


def test_extract_article_no_range_form():
    assert cn_parse.extract_article_no("第3-5條") == "第 3-5 條"


def test_extract_article_no_returns_none_without_article():
    assert cn_parse.extract_article_no("沒有條號的問題") is None
    assert cn_parse.extract_article_no("") is None


def test_extract_article_no_sub_article_is_DROPPED_known_gap():
    """⚠️ 已知落差，**不是**認可的行為 —— 這條測試存在的目的是讓它變得顯眼。

    docstring 宣稱「第10條之1」→「第 10-1 條」，實際回「第 10 條」，子條被丟掉。
    原因在 regex：`_ART_RE` 的中文分支寫成 `[數字]+(?:之[數字]+)?` 再接 `條`，
    要求「之」出現在「條」**之前**；但真實引用格式是「第十條之一」（之在條之後），
    於是 `_ART_RE` 在「條」就結束匹配，「之一」落在匹配之外被忽略。
    只有「第一之三條」這種不自然寫法才會走 之 分支。

    為什麼有風險：ingest 端 `normalize.py` 的 `ARTICLE_SPLIT_RE` 明確認得
    `之`／`-`／`／`，代表資料裡確實有子條；而 `article_no` 是原樣存進 Qdrant 的。
    若某條的 article_no 是「第10條之1」，這裡抽出的「第 10 條」就永遠比不中，
    精準分支失效後會掉回模糊／跨法競爭 —— 引用到**別條**。
    要修的話得動 regex（把 之/之N 移到條號之後），屬行為變更，未經同意不擅改。
    """
    assert cn_parse.extract_article_no("第10條之1") == "第 10 條"   # 之1 被丟掉
    assert cn_parse.extract_article_no("第十條之一") == "第 10 條"  # 之一 被丟掉
    # 之 分支只有這種不自然寫法才會觸發（之 在 條 之前）
    assert cn_parse.extract_article_no("第一之三條") == "第 1-3 條"


# ── keep_alive_value：型別決定 ollama 收不收得下 ──────────────────────────

def test_keep_alive_numeric_forms_return_int(monkeypatch):
    monkeypatch.setattr(gateway, "KEEP_ALIVE", "-1")
    assert gateway.keep_alive_value() == -1
    assert isinstance(gateway.keep_alive_value(), int)
    monkeypatch.setattr(gateway, "KEEP_ALIVE", " 600 ")
    assert gateway.keep_alive_value() == 600


def test_keep_alive_duration_forms_return_str(monkeypatch):
    """ollama 的 keep_alive 允許 "30m" 這種時長字串；傳成 number 會被 ollama 拒。"""
    monkeypatch.setattr(gateway, "KEEP_ALIVE", "30m")
    assert gateway.keep_alive_value() == "30m"
    assert isinstance(gateway.keep_alive_value(), str)


# ── host_label：URL → 主機 id ───────────────────────────────────────────

def test_host_label_localhost_is_this_host(monkeypatch):
    monkeypatch.setattr(gateway, "HOST_ID", "msi")
    assert gateway.host_label("http://127.0.0.1:11434") == "msi"
    assert gateway.host_label("http://localhost:8000") == "msi"


def test_host_label_compose_service_is_this_host(monkeypatch):
    """`qdrant` 這種 compose service 名沒有點，視為本機。"""
    monkeypatch.setattr(gateway, "HOST_ID", "msi")
    assert gateway.host_label("http://qdrant:6333") == "msi"


def test_host_label_from_configured_peer(monkeypatch):
    """設計重點：不寫死 IP 對照表，只認 HOST_API_URLS 裡配過的 peer。"""
    monkeypatch.setattr(gateway, "HOST_ID", "msi")
    monkeypatch.setattr(gateway, "HOST_API", {"x570": "https://api-x570.ragdemo.win/query"})
    assert gateway.host_label("https://api-x570.ragdemo.win/anything") == "x570"


def test_host_label_unknown_fqdn_returns_hostname(monkeypatch):
    monkeypatch.setattr(gateway, "HOST_ID", "msi")
    monkeypatch.setattr(gateway, "HOST_API", {})
    monkeypatch.setattr(gateway, "OLLAMA_LABELS", {})
    monkeypatch.setattr(gateway, "QDRANT_LABELS", {})
    assert gateway.host_label("https://stranger.example.com/x") == "stranger.example.com"


# ── active_llm_source ───────────────────────────────────────────────────

def test_active_llm_source_before_pick(monkeypatch):
    monkeypatch.setattr(gateway, "_bases", {})
    monkeypatch.setattr(gateway, "OLLAMA_URLS", ["http://127.0.0.1:11434"])
    monkeypatch.setattr(gateway, "OLLAMA_MODELS", ["qwen3:14b"])
    assert gateway.active_llm_source() == "http://127.0.0.1:11434 -> qwen3:14b"


def test_active_llm_source_after_pick(monkeypatch):
    monkeypatch.setattr(gateway, "_bases", {"ollama": "http://100.65.68.106:11434"})
    monkeypatch.setattr(gateway, "OLLAMA_URLS", ["http://127.0.0.1:11434", "http://100.65.68.106:11434"])
    monkeypatch.setattr(gateway, "OLLAMA_MODELS", ["qwen3:8b", "qwen3:14b"])
    assert gateway.active_llm_source() == "http://100.65.68.106:11434 -> qwen3:14b"


# ── law-update ops 通道：狀態機 ─────────────────────────────────────────

@pytest.fixture
def ops_dir(monkeypatch, tmp_path):
    """把 ops 目錄指到 tmp，並造一個 probe 檔讓 `_ops_path("probe")` 判定可用。"""
    d = tmp_path / ".ops"
    d.mkdir()
    (d / "probe").write_text("", encoding="utf-8")
    monkeypatch.setattr(rag, "_OPS_DIRS", (d,))
    monkeypatch.setattr(rag, "_LAW_VERSION_CACHE", (0, {}))
    return d


def test_law_update_state_idle(ops_dir):
    s = rag.law_update_state()
    assert s == {"pending": False, "running": False, "last": {}, "can_update": True}


def test_law_update_state_reads_worker_files(ops_dir):
    # 內容格式照 law-update-worker.sh:64 實際寫的 `{"started_at":"..."}`。
    (ops_dir / "law-update.running").write_text(
        json.dumps({"started_at": "2026-09-29T10:00:00+08:00"}), encoding="utf-8")
    (ops_dir / "law-update.status").write_text(
        json.dumps({"result": "ok"}), encoding="utf-8")
    s = rag.law_update_state()
    assert s["running"] is True
    assert s["pending"] is False
    assert s["last"] == {"result": "ok"}


def test_law_update_state_tolerates_corrupt_json(ops_dir):
    """壞檔不能讓 /law-update 整個 500（worker 寫到一半被讀到）。"""
    (ops_dir / "law-update.status").write_text("{壞掉的 json", encoding="utf-8")
    assert rag.law_update_state()["last"] == {}


def test_law_update_state_empty_running_file_reads_as_not_running(ops_dir):
    """⚠️ 記錄一個已知的窄競態，不是認可的行為。

    `law_update_state()` 用 `bool(_ops_read("law-update.running"))` 判斷，也就是
    「解析出來的 dict 是否為真」而不是「檔案是否存在」。worker 的
    `printf ... > "$RUN"` 會先 truncate 再寫入；若 worker 在這兩步之間被殺，
    檔案會是空的 → `_ops_read` 回 `{}` → `bool({})` 是 False → 對外顯示
    「沒有在執行」，於是可以再送一次請求，兩個 worker 疊在一起。

    worker 正常結束走 `trap 'rm -f "$RUN"' EXIT`（檔案不存在 → `_ops_read` 回 {}，
    結果一樣正確），所以要踩到必須是「truncate 與 write 之間被殺」。機率低但
    修法便宜：改成看檔案存不存在即可。此處不擅改行為，只把它釘住。
    """
    (ops_dir / "law-update.running").write_text("", encoding="utf-8")
    assert rag.law_update_state()["running"] is False
    # 對照組：正常內容 → True
    (ops_dir / "law-update.running").write_text(
        json.dumps({"started_at": "x"}), encoding="utf-8")
    assert rag.law_update_state()["running"] is True


def test_law_update_state_when_ops_unmounted(monkeypatch, tmp_path):
    """容器沒掛 data/.ops 時要回 can_update=False，而不是拋例外。"""
    monkeypatch.setattr(rag, "_OPS_DIRS", (tmp_path / "nope",))
    s = rag.law_update_state()
    assert s["can_update"] is False


def test_request_law_update_writes_request(ops_dir):
    r = rag.request_law_update("admin1")
    assert r["ok"] is True
    req = json.loads((ops_dir / "law-update.request").read_text(encoding="utf-8"))
    assert req["requested_by"] == "admin1"
    assert "requested_at" in req


def test_request_law_update_refuses_when_pending(ops_dir):
    """疊請求會讓兩個 worker 互相覆蓋，必須拒絕。"""
    assert rag.request_law_update("a")["ok"] is True
    second = rag.request_law_update("b")
    assert second["ok"] is False
    assert "排隊" in second["reason"]


def test_request_law_update_refuses_when_running(ops_dir):
    (ops_dir / "law-update.running").write_text(
        json.dumps({"started_at": "2026-09-29T10:00:00+08:00"}), encoding="utf-8")
    r = rag.request_law_update("a")
    assert r["ok"] is False
    assert "執行中" in r["reason"]


def test_request_law_update_refuses_when_ops_unmounted(monkeypatch, tmp_path):
    monkeypatch.setattr(rag, "_OPS_DIRS", (tmp_path / "nope",))
    r = rag.request_law_update("a")
    assert r["ok"] is False
    assert "ops" in r["reason"]


def test_ops_write_is_atomic_no_tmp_left_behind(ops_dir):
    """worker 可能正好在讀；.tmp → rename 才不會讀到寫一半的 JSON。"""
    assert rag._ops_write("law-update.request", {"a": 1}) is True
    assert not list(ops_dir.glob("*.tmp"))
    assert json.loads((ops_dir / "law-update.request").read_text(encoding="utf-8")) == {"a": 1}


# ── local_models / warmup ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_local_models_returns_names(monkeypatch):
    async def fake_req(kind, cands, method, path, **kw):
        return FakeResp(200, {"models": [{"name": "qwen3:14b"}, {"name": "bge-m3:latest"}]})

    monkeypatch.setattr(gateway, "_req", fake_req)
    assert await gateway.local_models() == ["qwen3:14b", "bge-m3:latest"]


@pytest.mark.asyncio
async def test_local_models_returns_empty_on_failure(monkeypatch):
    """registry 靠它記模型；掛掉要回空清單而不是讓心跳整個爆掉。"""
    async def boom(*a, **kw):
        raise RuntimeError("ollama down")

    monkeypatch.setattr(gateway, "_req", boom)
    assert await gateway.local_models() == []


@pytest.mark.asyncio
async def test_warmup_survives_ollama_not_ready(monkeypatch):
    """best-effort 契約：選不到主機就靜默放棄，api 仍要能上線。"""
    async def no_pick(*a, **kw):
        raise RuntimeError("no ollama")

    monkeypatch.setattr(gateway, "_pick", no_pick)
    await gateway.warmup()  # 不拋即為通過


# ── Qdrant 層 ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_collection_capabilities_detects_sparse(monkeypatch):
    """有命名向量+sparse → hybrid 路徑；單一未命名 dense → 舊 search。"""
    calls = {}

    async def fake_req(kind, cands, method, path, **kw):
        calls["path"] = path
        return FakeResp(200, {"result": {"config": {"params": {
            "vectors": {"dense": {"size": 1024, "distance": "Cosine"}},
            "sparse_vectors": {"sparse": {"modifier": "idf"}},
        }}}})

    monkeypatch.setattr(gateway, "_req", fake_req)
    monkeypatch.setattr(retrieve, "HAS_SPARSE", False)
    monkeypatch.setattr(retrieve, "_HAS_NAMED", False)
    await retrieve._collection_capabilities()
    assert retrieve.HAS_SPARSE is True
    assert retrieve._HAS_NAMED is True


@pytest.mark.asyncio
async def test_collection_capabilities_legacy_dense_only(monkeypatch):
    async def fake_req(kind, cands, method, path, **kw):
        return FakeResp(200, {"result": {"config": {"params": {
            "vectors": {"size": 1024, "distance": "Cosine"}}}}})

    monkeypatch.setattr(gateway, "_req", fake_req)
    monkeypatch.setattr(retrieve, "HAS_SPARSE", True)
    monkeypatch.setattr(retrieve, "_HAS_NAMED", True)
    await retrieve._collection_capabilities()
    assert retrieve.HAS_SPARSE is False
    assert retrieve._HAS_NAMED is False, "有 size 欄位 → 未命名 dense → 舊 search"


@pytest.mark.asyncio
async def test_collection_capabilities_survives_qdrant_down(monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("qdrant down")

    monkeypatch.setattr(gateway, "_req", boom)
    monkeypatch.setattr(retrieve, "HAS_SPARSE", True)
    await retrieve._collection_capabilities()  # 不拋
    assert retrieve.HAS_SPARSE is True, "偵測失敗時保留原狀，不要誤判成沒有 sparse"


@pytest.mark.asyncio
async def test_ensure_collection_skips_create_when_exists(monkeypatch):
    methods = []

    async def fake_req(kind, cands, method, path, **kw):
        methods.append(method)
        if method == "get":
            return FakeResp(200, {"result": {"config": {"params": {
                "vectors": {"dense": {"size": 1024}}, "sparse_vectors": {}}}}})
        return FakeResp(200, {})

    async def noop_names():
        return None

    monkeypatch.setattr(gateway, "_req", fake_req)
    monkeypatch.setattr(retrieve, "_ensure_law_names", noop_names)
    await retrieve.ensure_collection()
    assert "put" not in methods, "collection 已存在時不該重建（重建會掉資料）"


@pytest.mark.asyncio
async def test_upsert_includes_sparse_only_when_enabled(monkeypatch):
    sent = {}

    async def fake_embed(texts):
        return [[0.0] * 4 for _ in texts]

    async def fake_req(kind, cands, method, path, **kw):
        sent["points"] = kw.get("json", {}).get("points")
        return FakeResp(200, {})

    monkeypatch.setattr(gateway, "embed", fake_embed)
    monkeypatch.setattr(gateway, "_req", fake_req)
    monkeypatch.setattr(retrieve, "HAS_SPARSE", False)
    n = await retrieve.upsert([{"text": "第259條 違約金"}])
    assert n == 1
    assert "sparse" not in sent["points"][0]["vector"]

    monkeypatch.setattr(retrieve, "HAS_SPARSE", True)
    await retrieve.upsert([{"text": "第259條 違約金"}])
    assert "sparse" in sent["points"][0]["vector"]


# ── builtin_catalog ─────────────────────────────────────────────────────

def test_builtin_catalog_shape():
    cat = law_meta.builtin_catalog()
    assert cat, "內建目錄不該是空的"
    ids = [r["id"] for r in cat]
    assert len(ids) == len(set(ids)), "id 不可重複（前端當 key 用）"
    for row in cat:
        assert set(row) == {"id", "category", "label", "pattern", "sample_answer"}
        assert row["id"]
        assert row["category"] == "內建"
        assert isinstance(row["sample_answer"], str)


def test_builtin_catalog_threads_sample_law_through(monkeypatch):
    """確認 `sample_law` 真的被傳到 `_rule_answer`。

    不能靠「換法名答案就不同」來驗證：沒有 laws_meta 時 `_rule_answer` 一律回 "",
    兩邊都空字串，樣本資料下會得到 False 而測不出東西（踩過）。改為直接攔截
    `_rule_answer` 的參數，才是真的在驗「參數有沒有被傳下去」。
    """
    seen = []

    def fake_answer(intent, law):
        seen.append((intent, law))
        return f"{law}／{intent}"

    monkeypatch.setattr(law_meta, "_rule_answer", fake_answer)
    cat = law_meta.builtin_catalog("勞動基準法")
    assert seen, "_rule_answer 應被呼叫"
    assert all(law == "勞動基準法" for _intent, law in seen)
    assert all(row["sample_answer"].startswith("勞動基準法／") for row in cat)
