"""RAG 引擎：本地 rerank / 法律語意訊號 / 信心分級 / answer 閘門（低相關不問 LLM）。"""
import pytest

from app import rag


def test_detect_law_recognizes_bare_law_name():
    rag._LAW_NAMES = ["民法", "刑法", "證券交易法", "勞動基準法", "證券投資人及期貨交易人保護法"]
    try:
        assert rag._detect_law("證券交易法") == "證券交易法"
        assert rag._detect_law("請說明什麼是證券交易法") == "證券交易法"   # 法名開頭
        assert rag._detect_law("民法第259條的返還義務") is None           # 有條號時不誤觸
        assert rag._detect_law("今天天氣如何？") is None
        assert rag._detect_law("勞基法") == "勞動基準法"                     # 簡稱（別名）也認得
    finally:
        rag._LAW_NAMES = []


def test_decide_high_for_detected_law_in_hits():
    rag._LAW_NAMES = ["證券交易法"]
    try:
        top = [_hit(1)]  # 預設 law_name=民法 → 不該被判 high
        level, reason = rag._decide("證券交易法", top, 0.63)
        assert (level, reason) == ("medium", "cos@0.63")
        top2 = [{"id": 5, "score": 100.0, "_exact_rank": True,
                 "payload": {"law_name": "證券交易法", "article_no": "第 1 條",
                             "chapter": "", "text": "立法目的。"}}]
        level2, reason2 = rag._decide("證券交易法", top2, 0.63)
        assert level2 == "high" and "law_name" in reason2
    finally:
        rag._LAW_NAMES = []


def test_art_sort_key_orders_articles():
    assert rag._art_sort_key("第 1 條") < rag._art_sort_key("第 2 條")
    assert rag._art_sort_key("第 20-1 條") < rag._art_sort_key("第 100 條")
    assert rag._art_sort_key("第 3 條") < rag._art_sort_key("條文內容不分條")


# 基本法名與簡稱：最基本的名稱不能沒回應，答案只給基本敘述（不引用第1條）
LAW_QUERIES = [
    ("民法", "民法"),
    ("刑法", "刑法"),
    ("公司法", "公司法"),
    ("證券交易法", "證券交易法"),
    ("證交法", "證券交易法"),
    ("勞動基準法", "勞動基準法"),
    ("勞基法", "勞動基準法"),
    ("消費者保護法", "消費者保護法"),
    ("消保法", "消費者保護法"),
    ("個人資料保護法", "個人資料保護法"),
    ("個資法", "個人資料保護法"),
    ("民事訴訟法", "民事訴訟法"),
    ("民訴", "民事訴訟法"),
    ("刑事訴訟法", "刑事訴訟法"),
    ("刑訴", "刑事訴訟法"),
    ("行政訴訟法", "行政訴訟法"),
    ("行訴", "行政訴訟法"),
    ("道路交通管理處罰條例", "道路交通管理處罰條例"),
    ("道交條例", "道路交通管理處罰條例"),
    ("遺產及贈與稅法", "遺產及贈與稅法"),
    ("遺贈稅法", "遺產及贈與稅法"),
    ("證券交易稅條例", "證券交易稅條例"),
    ("證交稅", "證券交易稅條例"),
    ("所得稅法", "所得稅法"),
    ("稅捐稽徵法", "稅捐稽徵法"),
    ("戶籍法", "戶籍法"),
    ("土地法", "土地法"),
    ("強制執行法", "強制執行法"),
    ("強執法", "強制執行法"),
]


def _law_full_names() -> list[str]:
    return sorted({v for _, v in LAW_QUERIES})


def test_detect_law_basic_names_and_abbreviations():
    rag._LAW_NAMES = _law_full_names()
    rag._LAW_COUNTS = {n: 100 for n in _law_full_names()}
    try:
        for q, want in LAW_QUERIES:
            got = rag._detect_law(q)
            assert got == want, f"{q!r} 應對應 {want!r}，實際 {got!r}"
    finally:
        rag._LAW_NAMES = []
        rag._LAW_COUNTS = {}


def test_law_names_never_no_match():
    """最基本的名稱不能沒回應：即使 dense 偏低，法名精準命中仍判 high（不 no_match）。"""
    rag._LAW_NAMES = _law_full_names()
    try:
        for q, want in LAW_QUERIES:
            top = [{"id": 1, "score": 100.0, "_exact_rank": True,
                    "payload": {"law_name": want, "article_no": "第 1 條",
                                "chapter": "", "text": "立法目的。"}}]
            level, reason = rag._decide(q, top, 0.40)  # dense 極低也要有回應
            assert level != "no_match", f"{q!r} 不能沒回應（{level}/{reason}）"
            assert level == "high", f"{q!r} 法名命中應 high"
    finally:
        rag._LAW_NAMES = []


def test_law_brief_is_basic_without_article():
    rag._LAW_COUNTS = {"證券交易法": 209}
    try:
        brief = rag._law_brief("證券交易法")
        assert brief == "《證券交易法》（共209條）"
        assert "第1條" not in brief
    finally:
        rag._LAW_COUNTS = {}
    for name in _law_full_names():
        b = rag._law_brief(name)
        assert name in b
        assert "第1條" not in b, f"{name} 的基本敘述不該引第1條"
    assert rag._law_brief("未收錄之法") == "《未收錄之法》"  # 無篇數也不崩、仍有回應


def test_strip_article_refs_removes_any_article_mention():
    s = rag._strip_article_refs(
        "《證券交易法》規範有價證券之募集、發行與買賣等事項。"
        "相關內容可參見《證券交易法》第2條。本法共209條。")
    assert "第2條" not in s
    assert "可參見" not in s
    assert "《證券交易法》規範有價證券" in s
    assert "本法共209條" in s  # 非「第X條」的句尾保留

    assert rag._strip_article_refs("詳見勞基法第 10-1 條。") == ""
    assert rag._strip_article_refs("全是第2條。參見第1條。") == ""
    assert rag._strip_article_refs("") == ""
    assert rag._strip_article_refs("《刑法》規範犯罪與刑罰。") == "《刑法》規範犯罪與刑罰。"


def test_count_question_and_law_count_line():
    assert rag._is_count_question("證交法有多少條？")
    assert rag._is_count_question("勞基法共有幾條")
    assert rag._is_count_question("民法條文數是多少")
    assert not rag._is_count_question("證交法條文內容是什麼")
    assert not rag._is_count_question("證交法第22條")

    old_c, old_s = rag._LAW_COUNTS, rag._LAW_SUBS
    rag._LAW_COUNTS = {"證券交易法": 209}
    rag._LAW_SUBS = {"證券交易法": 43}
    try:
        line = rag._law_count_line("證券交易法")
        assert "共 209 條" in line and "主條 166" in line and "增訂子條 43" in line
        assert rag._law_count_line("不存在之法") is None
    finally:
        rag._LAW_COUNTS, rag._LAW_SUBS = old_c, old_s


def test_law_intent_router_and_rule_answer():
    assert rag._route_law_intent("證交法有多少條") == "count"
    assert rag._route_law_intent("證交法的主管機關是") == "authority"
    assert rag._route_law_intent("證交法何時施行") == "effective"
    assert rag._route_law_intent("證交法何時修正") == "revised"
    assert rag._route_law_intent("證交法修過幾次") == "rev_count"
    assert rag._route_law_intent("中華民國刑法是法律還是法規命令") == "level"
    assert rag._route_law_intent("證交法是否已廢止") == "active"
    assert rag._route_law_intent("什麼是證券交易法") == "brief"
    assert rag._route_law_intent("證交法管什麼事項") is None
    assert rag._route_law_intent("證交法和勞基法有什麼不同") is None

    assert rag._fmt_rm_date("19470101") == "民國 36 年 1 月 1 日（西元 1947）"
    assert rag._fmt_rm_date("20240807") == "民國 113 年 8 月 7 日（西元 2024）"
    assert rag._fmt_rm_date("") is None
    assert rag._fmt_rm_date("三十六年") == "三十六年"
    assert rag._meta_authority({"law_category": "行政＞金融監督管理委員會＞證券暨期貨管理目"}) == "金融監督管理委員會"
    assert rag._meta_authority({"law_category": "行政＞臺北市政府"}) == "臺北市政府"
    assert rag._meta_authority({"law_category": ""}) is None


def test_jev_gating_and_snippets():
    old_key, old_dis = rag.TYPESAFE_KEY, rag.JEV_DISABLED
    rag.TYPESAFE_KEY, rag.JEV_DISABLED = "", False
    try:
        assert not rag._jev_enabled()
    finally:
        rag.TYPESAFE_KEY, rag.JEV_DISABLED = old_key, old_dis

    hits = [
        {"payload": {"law_name": "證券交易法", "article_no": " 22 ",
                     "text": "有價證券之募集、發行、買賣之管理、監督之主管機關為金融監督管理委員會。"}},
        {"payload": {"law_name": "勞動基準法", "article_no": "38",
                     "text": "勞工在同一雇主或事業單位，繼續工作滿一定期間者，每年應依左列規定給予特別休假。"}},
    ]
    snips = rag._jev_snippets(hits)
    assert snips[0]["law"] == "證券交易法" and snips[0]["article"] == "22"
    assert snips[1]["law"] == "勞動基準法"
    assert len(rag._jev_snippets(hits, max_chars=8)[0]["text"]) <= 8


def test_rule_answer_from_meta():
    meta = {
        "證券交易法": {
            "law_level": "法律",
            "law_category": "行政＞金融監督管理委員會＞證券暨期貨管理目",
            "law_modified_date": "20240807",
            "law_effective_date": "20240807",
            "law_effective_note": "本法自公布日施行",
            "is_abandoned": False,
            "law_histories": "1.中華民國五十七年四月三十日總統令制定公布全文一百八十三條",
        },
        "已廢止法": {"law_level": "法律", "is_abandoned": True, "law_abandon_note": "已停止適用",
                      "law_category": "行政＞某部", "law_modified_date": "19900101"},
    }
    old_m, old_c, old_s = rag._LAW_META, rag._LAW_COUNTS, rag._LAW_SUBS
    rag._LAW_META = meta
    rag._LAW_COUNTS = {"證券交易法": 209}
    rag._LAW_SUBS = {"證券交易法": 43}
    try:
        a = rag._rule_answer("count", "證券交易法")
        assert "共 209 條" in a
        a = rag._rule_answer("authority", "證券交易法")
        assert a == "《證券交易法》的主管機關是 金融監督管理委員會。"
        a = rag._rule_answer("effective", "證券交易法")
        assert "民國 113 年 8 月 7 日" in a and "起施行" in a and "本法自公布日施行" in a
        assert rag._rule_answer("effective", "已廢止法") is None
        a = rag._rule_answer("revised", "證券交易法")
        assert "最近一次修正公布：民國 113 年 8 月 7 日" in a
        a = rag._rule_answer("rev_count", "證券交易法")
        assert "歷來共修正 1 次" in a
        a = rag._rule_answer("level", "證券交易法")
        assert a == "《證券交易法》位階屬「法律」。"
        a = rag._rule_answer("active", "證券交易法")
        assert "現行有效（未廢止）" in a
        a = rag._rule_answer("active", "已廢止法")
        assert "已廢止" in a and "已停止適用" in a
        a = rag._rule_answer("brief", "證券交易法")
        assert "金融監督管理委員會" in a and "現行有效條文 209 條" in a and "制定公布" in a
        assert rag._rule_answer("brief", "不存在之法") is None
        assert rag._rule_answer("count", "不存在之法") is None
    finally:
        rag._LAW_META, rag._LAW_COUNTS, rag._LAW_SUBS = old_m, old_c, old_s


def test_trace_joins_steps_with_fullwidth_bar():
    t = rag._trace("契約解除後回復原狀義務依何規定？", "第 259 條", 3, 0.73,
                   "high", "cos@0.73")
    p = t.split("｜")
    assert len(p) == 5
    assert p[0] == "條號:第 259 條"
    assert p[1] == "精準:3篇"
    assert p[2] == "dense:0.73(門檻0.58/0.62/0.70)"
    assert p[3] == "語意:有"
    assert p[4] == "判定:high(cos@0.73)"

    t2 = rag._trace("今天天氣如何？", None, 0, 0.50, "no_match", "low_relevance@0.50")
    p2 = t2.split("｜")
    assert p2[0] == "條號:無"
    assert p2[3] == "語意:無"
    assert p2[4] == "判定:no_match(low_relevance@0.50)"


def test_hit_view_jud_lists_all_judgment_values():
    # 一般命中：dense cosine｜sparse idf｜sum（融合分），以 「|」 分隔
    h = _hit(1, sparse=1.1012)
    h["_dense"] = 0.7350
    h["score"] = 1.2843
    v = rag._hit_view(h)
    assert v["jud"] == "dense cosine:0.7350|sparse idf:1.1012|sum:1.2843"
    # 條號精準分支：dense cosine（可能 n/a）＋ exact 分
    h2 = _hit(2, exact=True)
    h2["score"] = 12.3456
    v2 = rag._hit_view(h2)
    assert v2["jud"] == "dense cosine:n/a|exact:12.3456"
    # 精準分支有 dense 時也列出
    h3 = _hit(3, exact=True, dense=0.8123)
    h3["score"] = 5.0
    assert rag._hit_view(h3)["jud"] == "dense cosine:0.8123|exact:5.0000"


def test_law_urls_law_all_and_single_article():
    assert rag._law_url("A0000001") == "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=A0000001"
    assert rag._law_url("A0000001", "第 259 條") == \
        "https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=A0000001&flno=259"
    assert rag._law_url("A0000001", "第 10-1 條") == \
        "https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=A0000001&flno=10-1"
    assert rag._law_url(None) is None
    assert rag._law_url("A0000001", "不分條") == "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=A0000001"


def test_hit_view_url_law_only_vs_article():
    h = {"id": 9, "score": 0.5,
         "payload": {"pcode": "A0000001", "law_name": "民法", "article_no": "第 259 條",
                     "chapter": "", "text": "一段。"}}
    assert rag._hit_view(h)["url"] == \
        "https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=A0000001&flno=259"
    # law_only（法名查詢）→ 整部法連結
    assert rag._hit_view(h, law_only=True)["url"] == \
        "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=A0000001"
    # 無 pcode（判決等）→ 無連結
    h2 = {"id": 8, "score": 0.5, "payload": {"case_no": "111上1", "law": "刑法"}}
    assert rag._hit_view(h2)["url"] is None


def test_hit_view_law_brief_jud_and_badge():
    h = {"id": 9, "score": 0.0, "_exact_rank": True,
         "_brief": "《證券交易法》（共209條）",
         "payload": {"law_name": "證券交易法", "article_no": "第 2 條", "chapter": "", "text": "適用本法。"}}
    v = rag._hit_view(h)
    assert v["law"] is True
    assert v["jud"] == "法名:證券交易法｜《證券交易法》（共209條）"
    assert v["rel"] is None


def test_hit_view_rel_percent_and_exact_flag():
    h = _hit(1, dense=0.735)
    v = rag._hit_view(h)
    assert v["rel"] == 74            # 語意相似度 73.5% → 74%
    assert v["exact"] is False
    h2 = _hit(2, exact=True)         # 條號精準分支：無 dense 亦可顯示「精準」
    v2 = rag._hit_view(h2)
    assert v2["exact"] is True
    assert v2["rel"] is None
    h3 = _hit(3)                     # 無 dense 資料：保留 '?' 由前端兜底
    assert rag._hit_view(h3)["rel"] is None


def test_rerank_keeps_pool_dense_over_top_leg():
    # pool 已附真實 dense 時（_dense 非 None），頂層 dense leg map 不該覆寫成 None
    hits = [_hit(1, dense=0.73), _hit(2, dense=0.65)]
    out = rag.rerank("q", hits, dense_scores={}, top_k=2)
    assert out[0]["_dense"] == 0.73
    assert out[1]["_dense"] == 0.65


def _hit(iid, dense=None, exact=False, sparse=None):
    h = {"id": iid, "score": 0.5, "payload": {"law_name": "民法", "article_no": "第1條",
                                              "chapter": "", "text": "一段。"}}
    if exact:
        h["_exact_rank"] = True
    if dense is not None:
        h["_dense"] = dense
    if sparse is not None:
        h["_sparse"] = sparse
    return h


# ---------- 純函式 ----------

def test_rerank_exact_first_then_rest_by_dense():
    hits = [_hit(2, exact=True, dense=0.4), _hit(3, dense=0.7), _hit(1, dense=0.9)]
    out = rag.rerank("q", hits, dense_scores={}, top_k=3)
    assert [h["id"] for h in out] == [2, 1, 3]   # exact 領先；其餘依 dense 0.9 > 0.7 降序
    assert [h["_dense"] for h in out] == [0.4, 0.9, 0.7]


def test_rerank_sparse_only_sinks_to_bottom():
    # 稀疏方有分、dense 無（-1）→ 沉到最後
    hits = [_hit(1, dense=0.6), _hit(2)]
    out = rag.rerank("q", hits, dense_scores={}, top_k=2)
    assert [h["id"] for h in out] == [1, 2]


def test_rerank_top_k_cut():
    hits = [_hit(1), _hit(2), _hit(3), _hit(4)]
    out = rag.rerank("q", hits, dense_scores={1: 0.2, 2: 0.9, 3: 0.5, 4: 0.8}, top_k=2)
    assert [h["id"] for h in out] == [1, 2]
    assert [h["_dense"] for h in out] == [0.2, 0.9]


def test_legal_signal():
    assert rag._legal_signal("民法第259條之1返還義務") is True   # 條號
    assert rag._legal_signal("契約解除後當事人義務") is True      # 法律語彙
    assert rag._legal_signal("勞工特別休假怎麼算") is True        # 勞工/特別休假
    assert rag._legal_signal("今天天氣如何？") is False
    assert rag._legal_signal("煮咖哩的步驟與食材") is False
    assert rag._legal_signal("幫我訂一張到東京的機票") is False


def test_decide_levels():
    # 無候選 → no_match
    assert rag._decide("x", [], 0.0)[0] == "no_match"
    # 太弱（< 0.58）一律 no_match
    assert rag._decide("契約解除", [_hit(1, dense=0.30)], 0.30)[0] == "no_match"
    # 中間區間（0.60）：無法律語意 → no_match；有 → medium
    assert rag._decide("今天天氣如何", [_hit(1, dense=0.60)], 0.60)[0] == "no_match"
    assert rag._decide("契約解除", [_hit(1, dense=0.60)], 0.60)[0] == "medium"
    # >= 0.70 → high
    assert rag._decide("民法第259條", [_hit(1, dense=0.75)], 0.75)[0] == "high"
    # dense 偏低但「條號精準命中」→ 不誤判 no_match
    h = _hit(1, dense=0.30, exact=True)
    h["payload"]["article_no"] = "第259條"
    assert rag._decide("民法第259條", [h], 0.30)[0] == "medium"
    h2 = _hit(2, dense=0.30, exact=True)
    h2["payload"]["article_no"] = "第271條"
    assert rag._decide("民法第259條", [h2], 0.30)[0] == "no_match"   # 精準命中但條號不符 → 仍擋


# ---------- answer() 閘門（monkeypatch 網路） ----------

async def _patch(monkeypatch, dense_scores):
    async def fe(t):
        return [[0.0] * 16]
    async def fs(q, v, limit):
        return [_hit(1, exact=True)]
    async def fd(v, limit):
        return dense_scores
    def fr(q, h, d, topk):
        out = h[:topk]
        for x in out:
            x["_dense"] = d.get(x["id"])
        return out
    async def fprobe():
        return {}
    monkeypatch.setattr(rag, "embed", fe)
    monkeypatch.setattr(rag, "search", fs)
    monkeypatch.setattr(rag, "_dense_leg", fd)
    monkeypatch.setattr(rag, "rerank", fr)
    monkeypatch.setattr(rag, "_host_probe_log", fprobe)
    return monkeypatch


@pytest.mark.asyncio
async def test_answer_no_match_skips_llm(monkeypatch):
    called = {}
    async def fake_generate(q, c, cautious=False, model=""):
        called["llm"] = True
        return "LLM 產出"
    monkeypatch = await _patch(monkeypatch, {1: 0.20})
    monkeypatch.setattr(rag, "generate", fake_generate)
    r = await rag.answer("今天天氣如何？")
    assert r["ok"] is True
    assert r["no_match"] is True
    assert r["confidence"] == "no_match"
    assert "LLM 產出" not in r["answer"]      # 未呼叫 LLM
    assert "llm" not in called                 # generate 從未被呼叫
    assert "沒有符合比對" in r["answer"]


@pytest.mark.asyncio
async def test_answer_high_calls_llm(monkeypatch):
    called = {}
    async def fake_generate(q, c, cautious=False, model=""):
        called["cautious"] = cautious
        return "正常答案"
    monkeypatch = await _patch(monkeypatch, {1: 0.75})
    monkeypatch.setattr(rag, "generate", fake_generate)
    r = await rag.answer("契約解除後回復原狀之義務？")
    assert r["no_match"] is not True
    assert r["confidence"] == "high"
    assert "正常答案" in r["answer"]
    assert called.get("cautious") is False     # high 不需警示附註


@pytest.mark.asyncio
async def test_answer_medium_cautious_flag(monkeypatch):
    called = {}
    async def fake_generate(q, c, cautious=False, model=""):
        called["cautious"] = cautious
        return "謹慎答案"
    monkeypatch = await _patch(monkeypatch, {1: 0.60})
    monkeypatch.setattr(rag, "generate", fake_generate)
    r = await rag.answer("契約解除後回復原狀之義務？")   # 有法律語意
    assert r["confidence"] == "medium"
    assert called.get("cautious") is True      # medium 帶警示附註
    assert "謹慎答案" in r["answer"]


# ---------- 題庫第一關（pre-RAG）：identity 直接答；近似由 JEV 裁決；否決才進 RAG ----------

@pytest.mark.asyncio
async def test_answer_user_rule_identity_skips_rag(monkeypatch):
    rag._LAW_NAMES = []
    def fprobe(q, law):
        return {"rule": {"match": "惡意欠薪如何處理", "answer": "依勞基法須限期給付"}, "identity": True}
    async def fprobe_host():
        return {}
    async def fembed(t):
        raise AssertionError("identity 命中不該進 embed")
    monkeypatch.setattr(rag._rules, "probe", fprobe)
    monkeypatch.setattr(rag, "embed", fembed)
    monkeypatch.setattr(rag, "_host_probe_log", fprobe_host)
    r = await rag.answer("惡意欠薪如何處理")
    assert r["confidence"] == "user_rule"
    assert "限期給付" in r["answer"]
    assert "(identity)" in r["trace"]


@pytest.mark.asyncio
async def test_answer_user_rule_nev_adopt(monkeypatch):
    rag._LAW_NAMES = []
    def fprobe(q, law):
        return {"rule": {"match": "欠薪", "answer": "勞基法答"}, "identity": False}
    async def fjev(q, rule):
        return 0.85
    async def fprobe_host():
        return {}
    async def fembed(t):
        raise AssertionError("JEV 採用後不該進 embed")
    monkeypatch.setattr(rag._rules, "probe", fprobe)
    monkeypatch.setattr(rag, "_jev_rule_pick", fjev)
    monkeypatch.setattr(rag, "embed", fembed)
    monkeypatch.setattr(rag, "_host_probe_log", fprobe_host)
    r = await rag.answer("公司欠薪如何處理")
    assert r["confidence"] == "user_rule"
    assert "勞基法答" in r["answer"]
    assert "採題庫" in r["trace"]


@pytest.mark.asyncio
async def test_answer_user_rule_reject_falls_to_rag(monkeypatch):
    rag._LAW_NAMES = []
    def fprobe(q, law):
        return {"rule": {"match": "欠薪", "answer": "勞基法答"}, "identity": False}
    async def fjev(q, rule):
        return 0.30
    monkeypatch.setattr(rag._rules, "probe", fprobe)
    monkeypatch.setattr(rag, "_jev_rule_pick", fjev)
    monkeypatch = await _patch(monkeypatch, {1: 0.75})
    async def fake_generate(q, c, cautious=False, model=""):
        return "LLM 產出的答案"
    monkeypatch.setattr(rag, "generate", fake_generate)
    r = await rag.answer("公司欠薪如何處理")
    assert r["confidence"] == "high"
    assert "LLM 產出的答案" in r["answer"]
    assert "否決" in r["trace"]