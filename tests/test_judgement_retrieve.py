"""判決檢索的契約測試（spec 004 T018）。

## 這些測試不連 Qdrant

`retrieve.search_judgments()` 需要 gateway + Qdrant，所以這裡驗的是**契約**：
filter 形狀、payload 欄位、排序鍵、view 結構。這是 repo 既有慣例
（`tests/test_rag_pure.py` 也是這樣測 laws 路徑的純函式）。

真的 Qdrant 需要 gateway 可連線 —— 那屬於 integration，標記為
`judgement_corpus` 且在 service 不可用時 skip。

## 最重要的一條：laws 路徑必須完全不受影響

T018 是「extend, not replace」。若判決的 collection/filter 覆蓋了 laws 的，
症狀會是「法條查詢回傳判決片段」—— 不會報錯，只會給出看起來合理的錯誤答案。
所以這組測試裡有一條專門斷言 laws 的常數與 filter 未被改動。

## 不推論 court

FR-015 與 spec §Court 矛盾（見模組 docstring）。本層採後者：沒有 court 欄位、
沒有 court filter。`test_filter_has_no_court_key` 守住這點。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for sub in ("backend", "ingest/judgements"):
    p = ROOT / sub
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from app import retrieve  # noqa: E402

import chunk as CH  # noqa: E402
import document  # noqa: E402
import index_load as IL  # noqa: E402

DOCS = ROOT / "tests" / "fixtures" / "judgements" / "docs"
CIVIL = DOCS / "civil_6field.json"
CONSTITUTIONAL = DOCS / "constitutional_5field_empty_jpdf.json"
SINGLE = DOCS / "single_sentence_no_breaks.json"

ENTRY = "202607\\臺灣臺北地方法院民事\\TPDV,115,訴,2468,20260901,1.json"
CONST_ENTRY = "202607\\憲法法庭\\JCCC,115,審裁,1158,20260701.json"


def make_doc(p: Path = CIVIL, entry: str = ENTRY) -> document.JudgmentDocument:
    return document.from_document(
        json.loads(p.read_text(encoding="utf-8")),
        entry_path=entry,
        sha256="a" * 64,
    )


def hits_for(doc, *, size: int = 100, score: float = 0.9) -> list[dict]:
    """造出符合 T017 契約的 hits（不經過 Qdrant）。"""
    out = []
    for c in CH.chunk_document(doc, chunk_size=size):
        out.append({
            "id": c.point_id,
            "score": score,
            "payload": {
                "entry_path": doc.entry_path,
                "jid": doc.jid,
                "chunk_index": c.chunk_index,
                "start_offset": c.start_offset,
                "end_offset": c.end_offset,
                "content_hash": c.sha256,
                "jyear": doc.jyear,
                "jdate": doc.jdate,
                "jcase": doc.jcase,
            },
        })
    return out


# ── laws 路徑必須不受影響 ───────────────────────────────────────────────────

def test_laws_collection_constant_untouched():
    assert retrieve.COLLECTION == "laws"


def test_laws_base_filter_untouched():
    """laws 的 filter 不因判決而改變。

    `_BASE_CONDITIONS` 帶 laws 特有的旗標（is_repealed / is_abandoned），
    那些欄位在判決 payload 裡不存在。若判決的 filter 覆蓋了它，laws 查詢會
    拿到空結果或錯誤結果。
    """
    assert isinstance(retrieve._BASE_CONDITIONS, list)
    assert isinstance(retrieve._BASE_FILTER, dict)
    assert retrieve._BASE_FILTER["must"] is retrieve._BASE_CONDITIONS


def test_judgments_collection_is_separate():
    """判決用**不同的** collection 名稱。

    若共用 `COLLECTION`，設 `COLLECTION=judgements` 會讓法條檢索整個打到判決庫，
    症狀是「回傳看起來合理的判決片段」—— 不報錯，只是答錯。
    """
    assert retrieve.JUDGMENTS_COLLECTION == "judgements"
    assert retrieve.JUDGMENTS_COLLECTION != retrieve.COLLECTION


def test_judgment_filter_does_not_touch_laws_conditions():
    """判決的 filter 必須獨立建構，不得讀 `_BASE_CONDITIONS`。"""
    f = retrieve._judgment_filter()
    assert f["must"] == []
    # 回傳的是新的 list，不是 laws 的那個
    assert f["must"] is not retrieve._BASE_CONDITIONS


# ── payload 契約（T017 沿用）───────────────────────────────────────────────

def test_payload_fields_match_t017_contract():
    """必須與 ingest/judgements/index_load.py 的契約**完全一致**。

    兩邊各寫一份是危險的：改一邊忘了另一邊，upsert 寫進去的欄位與檢索期望的
    欄位就不同，而症狀是「filter 永遠匹配不到」。
    """
    assert retrieve.JUDGMENT_PAYLOAD_FIELDS == IL.PAYLOAD_FIELDS


def test_payload_fields_match_tasks_md():
    assert set(retrieve.JUDGMENT_PAYLOAD_FIELDS) == {
        "entry_path", "jid", "chunk_index", "start_offset", "end_offset",
        "content_hash", "jyear", "jdate", "jcase",
    }


def test_valid_payload_passes():
    retrieve.validate_judgment_payload(hits_for(make_doc())[0]["payload"])


@pytest.mark.parametrize("field", [
    "court", "statute_name", "article_number", "citation_status",
    "resolved_citation", "score", "embedding",
])
def test_forbidden_fields_are_rejected(field):
    """推論欄位一律拒收 —— 且訊息要說得出為什麼。"""
    p = dict(hits_for(make_doc())[0]["payload"])
    p[field] = "anything"
    with pytest.raises(retrieve.JudgmentPayloadError, match="禁止欄位"):
        retrieve.validate_judgment_payload(p)


def test_missing_field_is_rejected():
    p = dict(hits_for(make_doc())[0]["payload"])
    del p["jyear"]
    with pytest.raises(retrieve.JudgmentPayloadError, match="欄位不符"):
        retrieve.validate_judgment_payload(p)


def test_extra_field_is_rejected():
    p = dict(hits_for(make_doc())[0]["payload"])
    p["extra"] = 1
    with pytest.raises(retrieve.JudgmentPayloadError):
        retrieve.validate_judgment_payload(p)


def test_empty_jpdf_does_not_affect_payload():
    """空 `JPDF` 是合法狀態，不該讓 payload 少一個欄位或驗證失敗。

    `JPDF` 根本不在 payload 契約裡 —— payload 帶的是索引用得到的 metadata，
    而 JPDF 只影響 `store.py` 的 judgment 表。兩個邊界不同，不要混。
    """
    doc = make_doc(CONSTITUTIONAL, CONST_ENTRY)
    assert doc.jpdf == ""
    hits = hits_for(doc)
    retrieve.validate_judgment_payload(hits[0]["payload"])
    assert "jpdf" not in hits[0]["payload"]


def test_5field_jid_preserved_in_payload():
    """5-field JID 照原樣進 payload（FR-028），不得拆解。"""
    doc = make_doc(CONSTITUTIONAL, CONST_ENTRY)
    h = hits_for(doc)[0]
    assert h["payload"]["jid"] == "JCCC,115,審裁,1158,20260701"
    retrieve.validate_judgment_payload(h["payload"])


# ── point identity（T015 定義，檢索層不得重算）─────────────────────────────

def test_point_ids_come_from_chunk_not_regenerated():
    """檢索層的 hit ID 必須就是 `chunk.Chunk.point_id`。

    若檢索層自己產生 ID，兩邊公式會各自漂移，而症狀是「重跑變成新增而非覆寫」——
    同一份 chunk 產生兩個 point，而沒有人知道。
    """
    doc = make_doc()
    chunks = CH.chunk_document(doc, chunk_size=100)
    hits = hits_for(doc)
    assert [h["id"] for h in hits] == [c.point_id for c in chunks]


def test_point_id_is_64_hex_not_uuid():
    """point ID 是 sha256 hex，不是 Qdrant 自動產生的 UUID v4。"""
    for h in hits_for(make_doc()):
        retrieve.validate_judgment_point_id(h["id"], h["payload"])


def test_uuid_v4_point_id_is_rejected():
    """隨機 UUID 必須被擋 —— 那是「重跑變新增」的形狀。"""
    p = hits_for(make_doc())[0]["payload"]
    with pytest.raises(retrieve.JudgmentPayloadError, match="64 位 hex"):
        retrieve.validate_judgment_point_id("1b4e28ba-2fa1-11d2-883f-0016d3cca427", p)


def test_same_chunk_produces_same_point_id():
    """同一份 chunk 重複載入 → 相同 ID（覆寫而非重複）。"""
    doc = make_doc()
    a = [h["id"] for h in hits_for(doc)]
    b = [h["id"] for h in hits_for(make_doc())]
    assert a == b
    assert len(set(a)) == len(a)


def test_index_is_rebuildable_from_chunks():
    """Qdrant 是 derived：從 authoritative chunks 可重建出相同 identities。

    這條是「Qdrant 可以整個刪掉」的前提。沒有它，刪掉 collection 就等於永久
    失去那些 point 的識別。
    """
    doc = make_doc()
    first = [h["id"] for h in hits_for(doc)]
    # 模擬「索引遺失」：重新從 document 產生 chunks
    rebuilt = [h["id"] for h in hits_for(make_doc())]
    assert rebuilt == first


# ── filter ─────────────────────────────────────────────────────────────────

def test_filter_accepts_source_fields_only():
    f = retrieve._judgment_filter(jid="J1", jyear="115", jdate="20260901")
    keys = {c["key"] for c in f["must"]}
    assert keys == {"jid", "jyear", "jdate"}
    assert all("match" in c for c in f["must"])


def test_empty_filter_is_empty_must():
    assert retrieve._judgment_filter() == {"must": []}


def test_filter_has_no_court_key():
    """不得有 court filter —— 那是 FR-016 禁止的推論。

    FR-015 說 metadata 必須含 court，但 spec §Court 記載 source 沒有該欄位、
    且 7/494 的 `JFULL` line 0 不是法院名。兩者矛盾，本層採 §Court。
    """
    f = retrieve._judgment_filter(jid="J1", jyear="115", jdate="20260901")
    assert not any("court" in str(c).lower() for c in f["must"])


def test_filter_has_no_jcase_key():
    """`jcase` 刻意**不**作為 filter 參數。

    它是 opaque 代碼；把它當「案件類型」篩選是語意推論（FR-016）。corpus 裡
    1,336 個取值只被觀察過 0.46%。
    """
    import inspect

    sig = inspect.signature(retrieve._judgment_filter)
    assert "jcase" not in sig.parameters


def test_filter_does_not_use_laws_field_names():
    """不得帶 laws 專屬的欄位（is_repealed / article_no / law_name）。"""
    f = retrieve._judgment_filter(jid="J1")
    blob = json.dumps(f, ensure_ascii=False)
    for forbidden in ("is_repealed", "is_abandoned", "article_no", "law_name", "pcode"):
        assert forbidden not in blob


# ── deterministic 排序 ─────────────────────────────────────────────────────

def test_sort_key_is_descending_by_score():
    hits = [
        {"score": 0.5, "payload": {"jid": "A", "start_offset": 0, "chunk_index": 0}},
        {"score": 0.9, "payload": {"jid": "B", "start_offset": 0, "chunk_index": 0}},
    ]
    hits.sort(key=retrieve.judgment_sort_key)
    assert [h["score"] for h in hits] == [0.9, 0.5]


def test_sort_key_tiebreaks_on_jid():
    """同分時按 jid —— 不是靠 dict/set 的迭代順序。"""
    hits = [
        {"score": 0.8, "payload": {"jid": "B", "start_offset": 0, "chunk_index": 0}},
        {"score": 0.8, "payload": {"jid": "A", "start_offset": 0, "chunk_index": 0}},
    ]
    hits.sort(key=retrieve.judgment_sort_key)
    assert [h["payload"]["jid"] for h in hits] == ["A", "B"]


def test_sort_key_tiebreaks_on_offset_then_index():
    """同分同 jid → 按 offset，再按 chunk_index。"""
    hits = [
        {"score": 0.8, "payload": {"jid": "A", "start_offset": 200, "chunk_index": 2}},
        {"score": 0.8, "payload": {"jid": "A", "start_offset": 100, "chunk_index": 1}},
    ]
    hits.sort(key=retrieve.judgment_sort_key)
    assert [h["payload"]["start_offset"] for h in hits] == [100, 200]


def test_sort_is_stable_across_shuffles():
    """打亂輸入順序 → 排序結果完全相同。"""
    import random

    base = hits_for(make_doc(), size=50)
    expected = sorted(base, key=retrieve.judgment_sort_key)
    rng = random.Random(20260907)
    for _ in range(5):
        shuffled = list(base)
        rng.shuffle(shuffled)
        shuffled.sort(key=retrieve.judgment_sort_key)
        assert [h["id"] for h in shuffled] == [h["id"] for h in expected]


def test_sort_key_quantises_score():
    """分數量化到 6 位 —— 浮點末位差異不該把同分拆成兩組。

    若不量化，`0.8` 與 `0.8000000001` 會被視為不同分，而其中一個先跑
    tie-break，順序就依賴輸入順序。
    """
    a = {"score": 0.8, "payload": {"jid": "B", "start_offset": 0, "chunk_index": 0}}
    b = {"score": 0.8000000001, "payload": {"jid": "A", "start_offset": 0, "chunk_index": 0}}
    ka, kb = retrieve.judgment_sort_key(a), retrieve.judgment_sort_key(b)
    assert ka[0] == kb[0]
    # 而實際排序會走 tie-break → A 在前
    hits = [a, b]
    hits.sort(key=retrieve.judgment_sort_key)
    assert hits[0]["payload"]["jid"] == "A"


def test_sort_key_handles_missing_fields():
    """缺欄位不得拋錯 —— 回傳可排序的預設值。"""
    assert retrieve.judgment_sort_key({}) is not None
    assert retrieve.judgment_sort_key({"score": None, "payload": {}}) is not None


# ── hit view ───────────────────────────────────────────────────────────────

def test_hit_view_exposes_full_provenance():
    """每個 result 都必須能回答：哪份裁判、JID、哪個 chunk、offset、hash。"""
    doc = make_doc()
    view = retrieve.judgment_hit_view(hits_for(doc)[0])
    for field in ("entry_path", "jid", "chunk_index", "start_offset",
                  "end_offset", "content_hash", "jyear", "jdate", "jcase"):
        assert field in view, f"view 缺少 {field}"
    assert view["entry_path"] == doc.entry_path
    assert view["jid"] == doc.jid


def test_hit_view_does_not_reconstruct_text():
    """view 不得自行組裝 chunk 文字。

    payload 刻意不含 text（750 字 × N 筆會讓 collection 爆炸）。`has_text`
    讓呼叫端知道引用文字需要從 `JFULL` 切。
    """
    view = retrieve.judgment_hit_view(hits_for(make_doc())[0])
    assert view["text"] is None
    assert view["has_text"] is False


def test_hit_view_rejects_invalid_payload():
    h = hits_for(make_doc())[0]
    h["payload"] = {**h["payload"], "court": "臺北地院"}
    with pytest.raises(retrieve.JudgmentPayloadError):
        retrieve.judgment_hit_view(h)


def test_hit_view_has_no_court_or_score_field():
    """view 不得新增 court，也不得把 score 當成 payload 欄位。

    `score` 確實存在（那是查詢時的分數）但它是 hit 層級的，不是 payload 契約
    的一部分 —— 這裡驗的是「view 裡的 payload 欄位集合仍等於契約」。
    """
    view = retrieve.judgment_hit_view(hits_for(make_doc())[0])
    payload_keys = {k for k in view if k in set(retrieve.JUDGMENT_PAYLOAD_FIELDS)}
    assert payload_keys == set(retrieve.JUDGMENT_PAYLOAD_FIELDS)
    assert "court" not in view


# ── 檢索層不做的事 ─────────────────────────────────────────────────────────

def test_module_has_no_statute_resolution():
    import ast

    tree = ast.parse((ROOT / "backend" / "app" / "retrieve.py").read_text("utf-8"))
    funcs = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for forbidden in ("resolve_statute", "lookup_law_for_question", "infer_court",
                      "statute_name_of"):
        assert forbidden not in funcs


def test_judgment_section_does_not_import_gazetteer():
    """判決檢索這一段不得引入名單比對。

    檢查 T018 區段的**原始碼片段**（從「判決檢索」標記到 `_fusion_sort`）。
    片段不是合法 module，所以不能 parse 成 AST —— 用字串檢查就夠，因為這裡
    要抓的是「有沒有人貼進一份名單或一個 import」。
    """
    src = (ROOT / "backend" / "app" / "retrieve.py").read_text("utf-8")
    seg = src[src.index("# 判決檢索"):src.index("def _fusion_sort")]

    assert "import law_meta" not in seg
    assert "laws_meta" not in seg
    assert "gazetteer" not in seg.lower()


def test_query_does_not_trigger_external_lookup():
    """query 裡的「銀行法第XX條」不得觸發任何外部查詢。

    驗證方式：`search_judgments()` 裡沒有任何 `gateway` 之外的網路呼叫，
    而 gateway 的呼叫只在 Qdrant（本地/內網）。這裡靜態確認它只呼叫
    `_judgment_points_query`。
    """
    import ast

    tree = ast.parse((ROOT / "backend" / "app" / "retrieve.py").read_text("utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "search_judgments")
    called = {
        n.func.id for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert called <= {"max", "_judgment_filter", "_judgment_points_query",
                      "validate_judgment_payload", "range"}
    # 沒有 law_meta._detect_law / cn_parse.extract_article_no 那種 laws 專屬呼叫
    assert "law_meta" not in called
    assert "_detect_law" not in called
    assert "extract_article_no" not in called


def test_limit_zero_or_negative_returns_empty():
    """limit <= 0 → 空 list，不發請求。"""
    import asyncio

    async def run(limit):
        return await retrieve.search_judgments("q", [0.1] * 1024, limit=limit)

    for limit in (0, -1):
        assert asyncio.run(run(limit)) == []


def test_empty_query_with_vector_still_returns_hits():
    """空 query 字串不影響檢索 —— 向量才是查詢的載體。

    T020 定義的 empty-query 行為是「零結果 → 明確拒絕」，那是在 `rag.py`
    的回答層。檢索層不因為 query 字串為空而拒絕 —— 那會把一個正常的
    zero-result case 誤分類成「查詢無效」。
    """
    import ast

    src = (ROOT / "backend" / "app" / "retrieve.py").read_text("utf-8")
    seg = src[src.index("async def search_judgments"):]
    seg = seg[:seg.index("async def _judgment_points_query")]
    assert 'if not question' not in seg
    assert "strip()" not in seg


# ── Qdrant integration（需要服務，否則 skip）───────────────────────────────

def _qdrant_reachable() -> bool:
    """Qdrant 是否可連線。**不**需要 Ollama —— 這個測試用固定向量。"""
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen("http://localhost:6333/collections", timeout=2) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError):
        return False


needs_qdrant = pytest.mark.skipif(
    not _qdrant_reachable(),
    reason="本機沒有 Qdrant（T018–T020 的契約測試不需要它）",
)


@pytest.mark.judgement_corpus
@needs_qdrant
def test_integration_upsert_and_retrieve_round_trip():
    """真 Qdrant 的端到端：upsert → search → 相同 point ID。

    ## 為什麼用固定向量而不是 `gateway.embed()`

    embedding 需要 Ollama 與一個模型。那條路徑在本 feature 的 scope 之外
    （T017 的說明：embedding 是「定位原文的工具」，而產生向量屬於之後的事），
    而且使用者明確要求不啟動外部模型。

    固定向量足以驗證**契約**：point ID 的決定性、payload 的形狀、filter 的
    行為。那些與向量內容無關。
    """
    import asyncio
    import urllib.request

    from app import gateway

    doc = make_doc()
    hits = hits_for(doc, size=100)
    vec = [0.01] * 1024

    def _req(method, path, body=None):
        req = urllib.request.Request(
            f"http://localhost:6333{path}", method=method,
            data=(json.dumps(body).encode() if body is not None else None),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read() or b"{}")

    # 建 collection（冪等）
    try:
        _req("PUT", f"/collections/{retrieve.JUDGMENTS_COLLECTION}",
             {"vectors": {"dense": {"size": 1024, "distance": "Cosine"}}})
    except urllib.error.HTTPError:
        pass

    points = [
        {"id": h["id"], "vector": {"dense": vec}, "payload": h["payload"]}
        for h in hits
    ]
    _req("PUT", f"/collections/{retrieve.JUDGMENTS_COLLECTION}/points",
         {"points": points})

    # 檢索
    result = _req("POST", f"/collections/{retrieve.JUDGMENTS_COLLECTION}/points/query",
                  {"query": vec, "using": "dense", "limit": 10,
                   "with_payload": True})
    got = result["result"]["points"]
    assert got, "upsert 後應能查到"
    # 相同 point ID（重跑覆寫而非新增）
    assert {p["id"] for p in got} <= {h["id"] for h in hits}
    for p in got:
        retrieve.validate_judgment_payload(p["payload"])
