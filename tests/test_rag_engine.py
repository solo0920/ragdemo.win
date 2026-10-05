"""RAG 引擎：本地 rerank / 法律語意訊號 / 信心分級 / answer 閘門（低相關不問 LLM）。"""
import pytest

from app import rag, gateway, law_meta, law_struct, retrieve


def test_detect_law_recognizes_bare_law_name():
    law_meta._LAW_NAMES = ["民法", "刑法", "證券交易法", "勞動基準法", "證券投資人及期貨交易人保護法"]
    try:
        assert law_meta._detect_law("證券交易法") == "證券交易法"
        assert law_meta._detect_law("請說明什麼是證券交易法") == "證券交易法"   # 法名開頭
        # 法名＋條號 → 認得（要認得，才能走「該法該條」的精準分支）。
        # 這裡曾斷言 2 字法名「有條號時不誤觸」而回 None，但那是 len>=3 門檻的副作用，
        # 不是一致規則：3 字以上法名帶條號本來就會被認得（下面兩行即為證）。
        # 門檻放寬到 2 之後，民法／刑法不再成為唯一例外（2026-09-26 修）。
        assert law_meta._detect_law("民法第259條的返還義務") == "民法"
        assert law_meta._detect_law("刑法第19條故意傷害") == "刑法"
        assert law_meta._detect_law("勞動基準法第38條的罰則") == "勞動基準法"
        # 真正要防的是「問句沒提到任何法名」→ 不該憑空認出一部法
        assert law_meta._detect_law("今天天氣如何？") is None
        assert law_meta._detect_law("第259條的返還義務") is None   # 只有條號、沒法名
        assert law_meta._detect_law("勞基法") == "勞動基準法"                     # 簡稱（別名）也認得
    finally:
        law_meta._LAW_NAMES = []


def test_decide_high_for_detected_law_in_hits():
    law_meta._LAW_NAMES = ["證券交易法"]
    try:
        top = [_hit(1)]  # 預設 law_name=民法 → 不該被判 high
        level, reason = retrieve._decide("證券交易法", top, 0.63)
        assert (level, reason) == ("medium", "cos@0.63")
        top2 = [{"id": 5, "score": 100.0, "_exact_rank": True,
                 "payload": {"law_name": "證券交易法", "article_no": "第 1 條",
                             "chapter": "", "text": "立法目的。"}}]
        level2, reason2 = retrieve._decide("證券交易法", top2, 0.63)
        assert level2 == "high" and "law_name" in reason2
    finally:
        law_meta._LAW_NAMES = []


def test_art_sort_key_orders_articles():
    assert law_meta._art_sort_key("第 1 條") < law_meta._art_sort_key("第 2 條")
    assert law_meta._art_sort_key("第 20-1 條") < law_meta._art_sort_key("第 100 條")
    assert law_meta._art_sort_key("第 3 條") < law_meta._art_sort_key("條文內容不分條")


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
    law_meta._LAW_NAMES = _law_full_names()
    law_meta._LAW_COUNTS = {n: 100 for n in _law_full_names()}
    try:
        for q, want in LAW_QUERIES:
            got = law_meta._detect_law(q)
            assert got == want, f"{q!r} 應對應 {want!r}，實際 {got!r}"
    finally:
        law_meta._LAW_NAMES = []
        law_meta._LAW_COUNTS = {}


def test_law_names_never_no_match():
    """最基本的名稱不能沒回應：即使 dense 偏低，法名精準命中仍判 high（不 no_match）。"""
    law_meta._LAW_NAMES = _law_full_names()
    try:
        for q, want in LAW_QUERIES:
            top = [{"id": 1, "score": 100.0, "_exact_rank": True,
                    "payload": {"law_name": want, "article_no": "第 1 條",
                                "chapter": "", "text": "立法目的。"}}]
            level, reason = retrieve._decide(q, top, 0.40)  # dense 極低也要有回應
            assert level != "no_match", f"{q!r} 不能沒回應（{level}/{reason}）"
            assert level == "high", f"{q!r} 法名命中應 high"
    finally:
        law_meta._LAW_NAMES = []


def test_law_brief_is_basic_without_article():
    law_meta._LAW_COUNTS = {"證券交易法": 209}
    try:
        brief = law_meta._law_brief("證券交易法")
        assert brief == "《證券交易法》（共209條）"
        assert "第1條" not in brief
    finally:
        law_meta._LAW_COUNTS = {}
    for name in _law_full_names():
        b = law_meta._law_brief(name)
        assert name in b
        assert "第1條" not in b, f"{name} 的基本敘述不該引第1條"
    assert law_meta._law_brief("未收錄之法") == "《未收錄之法》"  # 無篇數也不崩、仍有回應


def test_strip_article_refs_removes_any_article_mention():
    s = law_meta._strip_article_refs(
        "《證券交易法》規範有價證券之募集、發行與買賣等事項。"
        "相關內容可參見《證券交易法》第2條。本法共209條。")
    assert "第2條" not in s
    assert "可參見" not in s
    assert "《證券交易法》規範有價證券" in s
    assert "本法共209條" in s  # 非「第X條」的句尾保留

    assert law_meta._strip_article_refs("詳見勞基法第 10-1 條。") == ""
    assert law_meta._strip_article_refs("全是第2條。參見第1條。") == ""
    assert law_meta._strip_article_refs("") == ""
    assert law_meta._strip_article_refs("《刑法》規範犯罪與刑罰。") == "《刑法》規範犯罪與刑罰。"


def test_count_question_and_law_count_line():
    assert law_meta._is_count_question("證交法有多少條？")
    assert law_meta._is_count_question("勞基法共有幾條")
    assert law_meta._is_count_question("民法條文數是多少")
    assert not law_meta._is_count_question("證交法條文內容是什麼")
    assert not law_meta._is_count_question("證交法第22條")

    old_c, old_s = law_meta._LAW_COUNTS, law_meta._LAW_SUBS
    law_meta._LAW_COUNTS = {"證券交易法": 209}
    law_meta._LAW_SUBS = {"證券交易法": 43}
    try:
        line = law_meta._law_count_line("證券交易法")
        assert "共 209 條" in line and "主條 166" in line and "增訂子條 43" in line
        assert law_meta._law_count_line("不存在之法") is None
    finally:
        law_meta._LAW_COUNTS, law_meta._LAW_SUBS = old_c, old_s


def test_law_intent_router_and_rule_answer():
    assert law_meta._route_law_intent("證交法有多少條") == "count"
    assert law_meta._route_law_intent("證交法的主管機關是") == "authority"
    assert law_meta._route_law_intent("證交法何時施行") == "effective"
    assert law_meta._route_law_intent("證交法何時修正") == "revised"
    assert law_meta._route_law_intent("證交法修過幾次") == "rev_count"
    assert law_meta._route_law_intent("中華民國刑法是法律還是法規命令") == "level"
    assert law_meta._route_law_intent("證交法是否已廢止") == "active"
    assert law_meta._route_law_intent("什麼是證券交易法") == "brief"
    assert law_meta._route_law_intent("證交法管什麼事項") is None
    assert law_meta._route_law_intent("證交法和勞基法有什麼不同") is None

    assert law_meta._fmt_rm_date("19470101") == "民國 36 年 1 月 1 日（西元 1947）"
    assert law_meta._fmt_rm_date("20240807") == "民國 113 年 8 月 7 日（西元 2024）"
    assert law_meta._fmt_rm_date("") is None
    assert law_meta._fmt_rm_date("三十六年") == "三十六年"
    assert law_meta._meta_authority({"law_category": "行政＞金融監督管理委員會＞證券暨期貨管理目"}) == "金融監督管理委員會"
    assert law_meta._meta_authority({"law_category": "行政＞臺北市政府"}) == "臺北市政府"
    assert law_meta._meta_authority({"law_category": ""}) is None


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
    old_m, old_c, old_s = law_meta._LAW_META, law_meta._LAW_COUNTS, law_meta._LAW_SUBS
    law_meta._LAW_META = meta
    law_meta._LAW_COUNTS = {"證券交易法": 209}
    law_meta._LAW_SUBS = {"證券交易法": 43}
    try:
        a = law_meta._rule_answer("count", "證券交易法")
        assert "共 209 條" in a
        a = law_meta._rule_answer("authority", "證券交易法")
        assert a == "《證券交易法》的主管機關是 金融監督管理委員會。"
        a = law_meta._rule_answer("effective", "證券交易法")
        assert "民國 113 年 8 月 7 日" in a and "起施行" in a and "本法自公布日施行" in a
        assert law_meta._rule_answer("effective", "已廢止法") is None
        a = law_meta._rule_answer("revised", "證券交易法")
        assert "最近一次修正公布：民國 113 年 8 月 7 日" in a
        a = law_meta._rule_answer("rev_count", "證券交易法")
        assert "歷來共修正 1 次" in a
        a = law_meta._rule_answer("level", "證券交易法")
        assert a == "《證券交易法》位階屬「法律」。"
        a = law_meta._rule_answer("active", "證券交易法")
        assert "現行有效（未廢止）" in a
        a = law_meta._rule_answer("active", "已廢止法")
        assert "已廢止" in a and "已停止適用" in a
        a = law_meta._rule_answer("brief", "證券交易法")
        assert "金融監督管理委員會" in a and "現行有效條文 209 條" in a and "制定公布" in a
        assert law_meta._rule_answer("brief", "不存在之法") is None
        assert law_meta._rule_answer("count", "不存在之法") is None
    finally:
        law_meta._LAW_META, law_meta._LAW_COUNTS, law_meta._LAW_SUBS = old_m, old_c, old_s


def test_trace_joins_steps_with_fullwidth_bar():
    t = retrieve._trace("契約解除後回復原狀義務依何規定？", "第 259 條", 3, 0.73,
                   "high", "cos@0.73")
    p = t.split("｜")
    assert len(p) == 5
    assert p[0] == "條號:第 259 條"
    assert p[1] == "精準:3篇"
    assert p[2] == "dense:0.73(門檻0.58/0.62/0.70)"
    assert p[3] == "語意:有"
    assert p[4] == "判定:high(cos@0.73)"

    t2 = retrieve._trace("今天天氣如何？", None, 0, 0.50, "no_match", "low_relevance@0.50")
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
    assert law_meta._law_url("A0000001") == "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=A0000001"
    assert law_meta._law_url("A0000001", "第 259 條") == \
        "https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=A0000001&flno=259"
    assert law_meta._law_url("A0000001", "第 10-1 條") == \
        "https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=A0000001&flno=10-1"
    assert law_meta._law_url(None) is None
    assert law_meta._law_url("A0000001", "不分條") == "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=A0000001"


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
    out = retrieve.rerank("q", hits, dense_scores={}, top_k=2)
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
    out = retrieve.rerank("q", hits, dense_scores={}, top_k=3)
    assert [h["id"] for h in out] == [2, 1, 3]   # exact 領先；其餘依 dense 0.9 > 0.7 降序
    assert [h["_dense"] for h in out] == [0.4, 0.9, 0.7]


def test_rerank_sparse_only_sinks_to_bottom():
    # 稀疏方有分、dense 無（-1）→ 沉到最後
    hits = [_hit(1, dense=0.6), _hit(2)]
    out = retrieve.rerank("q", hits, dense_scores={}, top_k=2)
    assert [h["id"] for h in out] == [1, 2]


def test_rerank_top_k_cut():
    hits = [_hit(1), _hit(2), _hit(3), _hit(4)]
    out = retrieve.rerank("q", hits, dense_scores={1: 0.2, 2: 0.9, 3: 0.5, 4: 0.8}, top_k=2)
    assert [h["id"] for h in out] == [1, 2]
    assert [h["_dense"] for h in out] == [0.2, 0.9]


def test_legal_signal():
    assert retrieve._legal_signal("民法第259條之1返還義務") is True   # 條號
    assert retrieve._legal_signal("契約解除後當事人義務") is True      # 法律語彙
    assert retrieve._legal_signal("勞工特別休假怎麼算") is True        # 勞工/特別休假
    assert retrieve._legal_signal("今天天氣如何？") is False
    assert retrieve._legal_signal("煮咖哩的步驟與食材") is False
    assert retrieve._legal_signal("幫我訂一張到東京的機票") is False


def test_decide_levels():
    # 無候選 → no_match
    assert retrieve._decide("x", [], 0.0)[0] == "no_match"
    # 太弱（< 0.58）一律 no_match
    assert retrieve._decide("契約解除", [_hit(1, dense=0.30)], 0.30)[0] == "no_match"
    # 中間區間（0.60）：無法律語意 → no_match；有 → medium
    assert retrieve._decide("今天天氣如何", [_hit(1, dense=0.60)], 0.60)[0] == "no_match"
    assert retrieve._decide("契約解除", [_hit(1, dense=0.60)], 0.60)[0] == "medium"
    # >= 0.70 → high
    assert retrieve._decide("民法第259條", [_hit(1, dense=0.75)], 0.75)[0] == "high"
    # dense 偏低但「條號精準命中」→ 不誤判 no_match
    h = _hit(1, dense=0.30, exact=True)
    h["payload"]["article_no"] = "第259條"
    assert retrieve._decide("民法第259條", [h], 0.30)[0] == "medium"
    h2 = _hit(2, dense=0.30, exact=True)
    h2["payload"]["article_no"] = "第271條"
    assert retrieve._decide("民法第259條", [h2], 0.30)[0] == "no_match"   # 精準命中但條號不符 → 仍擋


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
    monkeypatch.setattr(gateway, "embed", fe)
    monkeypatch.setattr(retrieve, "search", fs)
    monkeypatch.setattr(retrieve, "_dense_leg", fd)
    monkeypatch.setattr(retrieve, "rerank", fr)
    monkeypatch.setattr(gateway, "_host_probe_log", fprobe)
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


# ---------- 條號精準命中 → 直接引用 top1 條文，不問 LLM ----------

@pytest.mark.asyncio
async def test_answer_exact_article_quotes_verbatim_and_skips_llm(monkeypatch):
    """「法名＋條號」且條號精準命中 → 回條文原文，**完全不呼叫 LLM**。

    ⚠️ 這是 2026-10-05 實測修的 bug。症狀：問「證券交易法第15條」回的是
    「《證券交易法》（共209條）」—— 沒有第15條的內容。

    根因是 `brief_law` 分支（`answer()` 裡 `if brief_law and top and top[0]...`）
    只看「有沒有偵測到法名」＋「top1 是不是那部法」，**沒有排除有條號的情況**
    → 「證券交易法**第15條**」被當成「查《證券交易法》這部法」而回簡介。
    而且那個分支還會 `_strip_article_refs()` 把條號洗掉，LLM 答對也會被抹掉。

    為什麼可以跳過 LLM：條號精準是**字面相同**的條文，不是推論。讓 LLM 改寫
    只會失真（實測 JEV 0.31 判定不合格而退回規則卡，等於白花一次呼叫）。
    """
    law_meta._LAW_NAMES = ["證券交易法"]
    called = {}
    async def fake_generate(q, c, cautious=False, brief_law=None, model=""):
        called["llm"] = True
        return "LLM 產出（不該被用到）"
    monkeypatch = await _patch(monkeypatch, {1: 0.66})
    monkeypatch.setattr(rag, "generate", fake_generate)

    async def fs(q, v, limit):
        # 條號字面相同 —— 這正是「精準命中」的定義
        h = _hit(1, exact=True)
        h["payload"].update(law_name="證券交易法", article_no="第 15 條",
                            text="依本法經營之證券業務，其種類如左：一、有價證券之承銷。")
        return [h]
    monkeypatch.setattr(retrieve, "search", fs)

    try:
        r = await rag.answer("證券交易法第15條")
    finally:
        law_meta._LAW_NAMES = []

    assert "llm" not in called, "條號精準命中時不該呼叫 LLM"
    assert "LLM 產出" not in r["answer"]
    assert "依本法經營之證券業務" in r["answer"], r["answer"]
    # 必須是「條文的內容」，不是法名簡介
    assert "共209條" not in r["answer"], "又退回法名簡介了（brief_law 分支吃掉了它）"
    assert r["confidence"] == "rule"       # 逐字引用、非生成
    assert "條號精準" in r["trace"]


@pytest.mark.asyncio
async def test_exact_article_keeps_paragraph_newlines(monkeypatch):
    """條號精準分支**必須保留換行** —— 換行是項次的分隔，不是排版雜訊。

    ⚠️ 這是 2026-10-05 使用者回報的迴歸，由前一個修復自己引入：該分支原本用
    `collapse_ws(text)` 取值，而 corpus 的 text 是用 `\\n` 分隔各項
    （證券交易法第14條 ＝「本法所稱財務報告…\\n前項…\\n第一項…」共 6 段）。
    `collapse_ws()` 的定義就是「把連續空白（含換行）壓成單一空格」，於是 6 項
    黏成一坨、款次標籤（「一、」「（一）」）看起來像消失了 —— 使用者拿司法院
    原文比對，發現項次不見。

    為什麼這是「結構」而不只是排版：`law_struct.structure()` 靠 `\\n` 算出
    「1項6款」、`cite_item()` 靠它顯示「第N項」。換行被壓掉，那兩個函式看到的
    就是「全文 1 段」。所以壓掉換行不只是難看，是**資訊遺失**。
    """
    law_meta._LAW_NAMES = ["證券交易法"]
    monkeypatch = await _patch(monkeypatch, {1: 0.63})

    async def never_called(*a, **k):
        raise AssertionError("條號精準命中時不該呼叫 LLM")
    monkeypatch.setattr(rag, "generate", never_called)

    six_paras = (
        "本法所稱財務報告，指發行人及證券商依法令規定應定期編送之財務報告。\n"
        "前項財務報告之內容，由主管機關定之。\n"
        "第一項財務報告應經董事長簽名或蓋章。\n"
        "前項會計主管應具備一定之資格條件。\n"
        "股票已在證券交易所上市者，應揭露薪資報酬政策。\n"
        "前項公司應於章程訂明盈餘提撥比率。"
    )
    async def fs(q, v, limit):
        h = _hit(1, exact=True)
        h["payload"].update(law_name="證券交易法", article_no="第 14 條", text=six_paras)
        return [h]
    monkeypatch.setattr(retrieve, "search", fs)

    try:
        r = await rag.answer("證券交易法第14條")
    finally:
        law_meta._LAW_NAMES = []

    # 去掉 "x570: " 前綴後才是條文本身
    body = r["answer"].split(": ", 1)[1] if ": " in r["answer"] else r["answer"]
    assert body.count("\n") == 5, f"6 項應該有 5 個換行分隔，實際 {body.count(chr(10))}：{body!r}"
    for frag in ("前項財務報告之內容", "第一項財務報告應經董事長", "前項公司應於章程"):
        assert frag in body, f"項次黏掉了：{frag}"
    # 結構解析器仍看得懂（這才是換行的實際用途）
    assert law_struct.structure(body)["para"] == 6, law_struct.structure(body)
    assert law_struct.cite_item(body) == "第1項"


@pytest.mark.asyncio
async def test_exact_article_does_not_fire_when_article_number_differs(monkeypatch):
    """條號**不一致**時不能直接引用 —— 否則「第15條」會答成別的條文。

    這條守住 `_exact_match()` 的邊界：它比對的是 article_no，不是「有沒有條號」。
    放寬成「有條號就答 top1」是這個修法最危險的退化成��。
    """
    law_meta._LAW_NAMES = ["證券交易法"]
    called = {}
    async def fake_generate(q, c, cautious=False, brief_law=None, model=""):
        called["llm"] = True
        return "LLM 產出"
    monkeypatch = await _patch(monkeypatch, {1: 0.66})
    monkeypatch.setattr(rag, "generate", fake_generate)

    async def fs(q, v, limit):
        # 條號是第 99 條，但使用者問第 15 條 → 不是精準命中
        h = _hit(1, exact=True)
        h["payload"].update(law_name="證券交易法", article_no="第 99 條",
                            text="第九十九條的內容。")
        return [h]
    monkeypatch.setattr(retrieve, "search", fs)

    try:
        r = await rag.answer("證券交易法第15條")
    finally:
        law_meta._LAW_NAMES = []

    assert called.get("llm"), "條號不符時應走一般 RAG（問 LLM），不是直接引用"
    assert "第九十九條的內容" not in r["answer"], "不該引用條號不符的條文"
    assert r["confidence"] != "rule"


@pytest.mark.asyncio
async def test_bare_law_name_still_gets_the_brief(monkeypatch):
    """純法名查詢（無條號）**仍然**回簡介 —— 別被新分支誤傷。

    條號精準分支的前提是 `an`（條號）存在；沒有條號時必須維持原行為。
    """
    law_meta._LAW_NAMES = ["證券交易法"]
    monkeypatch = await _patch(monkeypatch, {1: 0.68})
    monkeypatch.setattr(rag, "law_meta", law_meta)
    async def fake_generate(q, c, cautious=False, brief_law=None, model=""):
        return f"《{brief_law}》規範有價證券之募集與買賣。"
    monkeypatch.setattr(rag, "generate", fake_generate)

    async def fs(q, v, limit):
        h = _hit(1)
        h["payload"].update(law_name="證券交易法", article_no="第 1 條", text="立法目的。")
        return [h]
    monkeypatch.setattr(retrieve, "search", fs)

    try:
        r = await rag.answer("證券交易法")
    finally:
        law_meta._LAW_NAMES = []

    assert r["confidence"] != "rule", "純法名不該走條號精準分支"
    assert "規範有價證券" in r["answer"], r["answer"]
    assert "條號精準" not in r["trace"]


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
    law_meta._LAW_NAMES = []
    def fprobe(q, law):
        return {"rule": {"match": "惡意欠薪如何處理", "answer": "依勞基法須限期給付"}, "identity": True}
    async def fprobe_host():
        return {}
    async def fembed(t):
        raise AssertionError("identity 命中不該進 embed")
    monkeypatch.setattr(rag._rules, "probe", fprobe)
    monkeypatch.setattr(gateway, "embed", fembed)
    monkeypatch.setattr(gateway, "_host_probe_log", fprobe_host)
    r = await rag.answer("惡意欠薪如何處理")
    assert r["confidence"] == "user_rule"
    assert "限期給付" in r["answer"]
    assert "(identity)" in r["trace"]


@pytest.mark.asyncio
async def test_answer_user_rule_nev_adopt(monkeypatch):
    law_meta._LAW_NAMES = []
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
    monkeypatch.setattr(gateway, "embed", fembed)
    monkeypatch.setattr(gateway, "_host_probe_log", fprobe_host)
    r = await rag.answer("公司欠薪如何處理")
    assert r["confidence"] == "user_rule"
    assert "勞基法答" in r["answer"]
    assert "採題庫" in r["trace"]


@pytest.mark.asyncio
async def test_answer_user_rule_reject_falls_to_rag(monkeypatch):
    law_meta._LAW_NAMES = []
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