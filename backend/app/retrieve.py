"""檢索本體：Qdrant 讀寫、dense/sparse 融合、rerank，以及「要不要回答」的判定。

從 rag.py 切出來的理由：這是整個專案唯一會決定「引用哪一條條文」的層，把它跟
provider 呼叫（openrouter／gemini／cohere…）與 ops 檔案通道混在同一個檔案裡，
使得「改檢索」與「改 LLM 路由」看起來是同一種改動 —— 它們其實無關。

分層：retrieve 依賴 gateway（HTTP）、law_meta（法名／條號知識）、cn_parse（條號
抽取），不依賴 rag.py。rag.py 依賴 retrieve，不反向依賴。
"""
import asyncio
import json
import logging
import os
import uuid

from . import cn_parse, gateway, law_meta
from . import law_struct as _law
from .common import sparse as _sparse

logger = logging.getLogger("ragdemo")

RERANK_MODEL = os.getenv("RERANK_MODEL") or "qllama/bge-reranker-v2-m3:latest"
COLLECTION = os.getenv("COLLECTION") or "laws"
DIM = 1024  # bge-m3 向量維度
# 相關性/信心閘門（校準自本機量測：正題 top dense 0.64–0.76、無關語意題 0.43–0.57）：
# - dense 命中 < RAG_MIN_DENSE   → 直接 no_match（低相關，不問 LLM）
# - 無「法律語意訊號」且 < RAG_MID_DENSE → no_match（不明語意不猜）
# - >= RAG_HIGH_DENSE → high；否則 medium（生成時加「不確定就明說」附註）
MIN_DENSE = float(os.getenv("RAG_MIN_DENSE") or "0.58")
MID_DENSE = float(os.getenv("RAG_MID_DENSE") or "0.62")
HIGH_DENSE = float(os.getenv("RAG_HIGH_DENSE") or "0.70")
# collection 能力偵測：有 sparse 命名向量 → 走 hybrid(DBSF)；單一未命名 dense → 舊 search。
HAS_SPARSE = False
_HAS_NAMED = False
# ⚠️ hybrid 查詢過濾：**不再排除**已廢止／已中止條文（2026-10-05 反轉）。
#
# 舊版在這裡過濾掉 `is_repealed` / `is_abandoned`，配合 ingest 的排除，
# 廢止條文完全不存在於檢索面。實測（證券交易法）：229 條只有 209 條可查，
# 第9條／第17條等 20 條「（刪除）」查不到 —— 而且**沒有任何徵兆**。
#
# 那個設計有個更根本的問題：**使用者問「證券交易法第9條」時，正確答案是
# 「第9條已刪除」，不是「查不到」**。查不到會被誤讀成「系統沒有這部法」
# 或「條號寫錯了」，而兩者都是錯的。
#
# 現在的取捨：廢止條文**可以被查到**（所以 `_hit_view` 與回答層要明確標示
# 已廢止），但排序時**不與現行條文同等對待** —— 那是 `_decide()` 的判斷，
# 不是檢索層的過濾。分層的好處是資料層誠實、呈現層負責判斷。
_BASE_CONDITIONS: list[dict] = []
_BASE_FILTER: dict = {"must": _BASE_CONDITIONS}
async def _collection_capabilities() -> None:
    """讀 collection 設定，記錄 HAS_SPARSE/_HAS_NAMED（供 search 選路）。"""
    global HAS_SPARSE, _HAS_NAMED
    try:
        r = await gateway._req("qdrant", gateway.QDRANT_URLS, "get", f"/collections/{COLLECTION}", timeout=30)
        if r.status_code != 200:
            return
        params = r.json()["result"]["config"]["params"]
        vectors = params.get("vectors", {})
        _HAS_NAMED = isinstance(vectors, dict) and "size" not in vectors
        HAS_SPARSE = bool(params.get("sparse_vectors"))
    except Exception as e:
        logger.warning("collection 能力偵測失敗（%s），退回舊 search", e)
async def ensure_collection() -> None:
    await _collection_capabilities()
    await _ensure_law_names()  # 法名清單：collection 通常已存在，也會提前建
    r = await gateway._req("qdrant", gateway.QDRANT_URLS, "get", f"/collections/{COLLECTION}", timeout=30)
    if r.status_code == 200:
        return
    r = await gateway._req("qdrant", gateway.QDRANT_URLS, "put", f"/collections/{COLLECTION}", timeout=30,
                   json={"vectors": {"dense": {"size": DIM, "distance": "Cosine"}},
                         "sparse_vectors": {"sparse": {"modifier": "idf"}}})
    r.raise_for_status()
    await _collection_capabilities()
    await _ensure_law_names()
async def _ensure_law_names() -> None:
    """scroll 全量 payload（law_name＋article_no）收集法名與條文數，best-effort：失敗則留空、法名分支略過。"""
    # 這三個 corpus 統計住在 law_meta（是「有哪些法」的知识，不是檢索狀態）。
    # 從別的模組賦值要用 `law_meta.X = ...`，不能宣告 global（跨模組無效）。
    if law_meta._LAW_NAMES:
        return
    seen: dict[str, set[str]] = {}
    subs: dict[str, int] = {}
    offset = None
    prev = None
    try:
        while True:
            body = {"limit": 5000, "with_payload": ["law_name", "article_no"], "with_vector": False}
            if offset is not None:
                body["offset"] = offset
            r = await gateway._req("qdrant", gateway.QDRANT_URLS, "post",
                           f"/collections/{COLLECTION}/points/scroll", json=body, timeout=60)
            r.raise_for_status()
            pts = r.json()["result"]["points"]
            for p in pts:
                pl = p.get("payload", {})
                n, an = pl.get("law_name"), pl.get("article_no")
                if not (n and an):
                    continue
                s = seen.setdefault(n, set())
                if an not in s:
                    s.add(an)
                    if "-" in an.replace(" ", ""):
                        subs[n] = subs.get(n, 0) + 1
            if not pts:
                break
            prev, offset = offset, pts[-1]["id"]
            # 近 u64 上限的點（u64 id 換算高於 i64 等）scroll 永不前進 → 防死循環
            if offset == prev:
                break
    except Exception:
        return
    law_meta._LAW_COUNTS = {n: len(s) for n, s in seen.items()}
    law_meta._LAW_SUBS = subs
    law_meta._LAW_NAMES = sorted(seen, key=lambda n: (-len(seen[n]), n))
    law_meta._try_load_law_meta()
async def upsert(docs: list[dict]) -> int:
    vecs = await gateway.embed([d["text"] for d in docs])
    points = [
        {"id": str(uuid.uuid4()),
         "vector": {"dense": v} | ({"sparse": _sparse.sparse_vector(d["text"])} if HAS_SPARSE else {}),
         "payload": d}
        for v, d in zip(vecs, docs)
    ]
    r = await gateway._req("qdrant", gateway.QDRANT_URLS, "put", f"/collections/{COLLECTION}/points",
                   json={"points": points})
    r.raise_for_status()
    return len(points)
async def _points_query(body: dict) -> list[dict]:
    r = await gateway._req("qdrant", gateway.QDRANT_URLS, "post", f"/collections/{COLLECTION}/points/query",
                   json=body, timeout=30)
    r.raise_for_status()
    return r.json()["result"]["points"]
def _fusion_sort(hits: list[dict]) -> list[dict]:
    """本端 DBSF 融合（決定性）：dense 餘弦與 sparse idf-score 各自 min-max 正規化後加總。
    不用 RRF：RRF 只看排名，熱門條號(如「第11條」)兩腿都被灌滿時，真身(e.g. 證交法11)
    在 sparse 腿排到上百名，被融合丟掉；「同時命中兩組 token」的文件會勝出。"""
    dvals = [h["_dense"] for h in hits if h.get("_dense") is not None]
    svals = [h["_sparse"] for h in hits if h.get("_sparse") is not None]
    dlo, dhi = (min(dvals), max(dvals)) if dvals else (0.0, 0.0)
    slo, shi = (min(svals), max(svals)) if svals else (0.0, 0.0)

    def norm(x, lo, hi):
        return (x - lo) / (hi - lo) if hi > lo else 0.0

    for h in hits:
        h["_fused"] = norm(h.get("_dense") or 0.0, dlo, dhi) + norm(h.get("_sparse") or 0.0, slo, shi)
    return sorted(hits, key=lambda h: h["_fused"], reverse=True)
async def search(question: str, vector: list[float], limit: int = 50) -> list[dict]:
    """召回：dense(bge-m3)＋sparse(TF) 兩腿分開查，本端 DBSF 融合（決定性、可控）。
    filter 只吃現行條文（is_repealed/abandoned=false）。"""
    prefetch = max(limit, 500)
    if HAS_SPARSE:
        dense_pts = await _points_query({"query": vector, "using": "dense", "limit": prefetch,
                                         "filter": _BASE_FILTER, "with_payload": True})
        sq = _sparse.sparse_vector(question)
        sparse_pts: list[dict] = []
        if sq["indices"]:
            sparse_pts = await _points_query(
                {"query": {"indices": sq["indices"], "values": sq["values"]},
                 "using": "sparse", "limit": prefetch, "filter": _BASE_FILTER, "with_payload": True})
        pool: dict[int, dict] = {}
        for p in dense_pts:
            pool[p["id"]] = {"id": p["id"], "payload": p["payload"], "_dense": p["score"], "_sparse": 0.0}
        for p in sparse_pts:
            e = pool.setdefault(p["id"], {"id": p["id"], "payload": p["payload"], "_dense": 0.0, "_sparse": 0.0})
            e["_sparse"] = p["score"]
        hits = _fusion_sort(list(pool.values()))[:limit]
        for h in hits:
            h["score"] = h["_fused"]
            h.pop("_fused", None)
    else:
        # 純 dense（laws collection 目前沒有 sparse vectors → HAS_SPARSE=False）。
        # /points/search 對具名向量要 {"name":..,"vector":..}；{"dense": ..} 是
        # /points/upsert 與 /points/query+using 的形式，在這裡會被 400
        # （"did not match any variant of untagged enum NamedVectorStruct"）。
        body = ({"vector": {"name": "dense", "vector": vector}} if _HAS_NAMED
                else {"vector": vector})
        body.update({"limit": limit, "with_payload": True})
        r = await gateway._req("qdrant", gateway.QDRANT_URLS, "post", f"/collections/{COLLECTION}/points/search",
                       json=body, timeout=30)
        r.raise_for_status()
        hits = r.json()["result"]
    # 條號精準分支：query 含「第N條」時，同時對「同條號、跨法」候選以 dense 打分，
    # 破除熱門條號被擁擠（例「證券交易法第20條」sparse 腿排到數百名外）與"湊巧含法名子串"
    # 的文件搶位的問題；法名+條號組合下真正的條文會衝到最前。
    an = cn_parse.extract_article_no(question)
    if an:
        # 法名＋條號（含簡稱，例:「勞基法第38條」）：直接滾「該法該條」當精準來源。
        # 為什麼不能只靠「跨法同條號」競爭（實測）：
        # ① query 為「法名＋第N條」時 sparse tokenizer 的 CJK run 把「第」吃進法名 bigram，
        #    數字被 latin 拆出 → 根本沒有「第38條」條號 token；② doc 端 min(tf,4) 飽和讓
        #    罰責類條文的「規定/條規」高頻字拿 3~4 權重，與 query「規定什麼」假重疊→虛高
        #    sparse dot（事業用爆炸物管理條例38 got 7 vs 勞動基準法38 got 2，語意無關卻霸榜）；
        #    ③ 簡稱「勞基法」對法名「勞動基準法」bigram 重疊=0，加分失效、正確條文掉到 top5。
        # 偵測到法名→鎖該法該條；僅條號查詢（無法名）仍走下方跨法競爭。
        law = law_meta._detect_law(question)
        if law:
            f_law = {"must": _BASE_CONDITIONS +
                     [{"key": "law_name", "match": {"value": law}},
                      {"key": "article_no", "match": {"value": an}}]}
            r = await gateway._req("qdrant", gateway.QDRANT_URLS, "post",
                           f"/collections/{COLLECTION}/points/scroll",
                           json={"filter": f_law, "limit": 10,
                                 "with_payload": True, "with_vector": False}, timeout=30)
            r.raise_for_status()
            solo = [h for h in r.json()["result"]["points"] if h.get("payload")]
            if solo:
                qv = _sparse.sparse_vector(question)
                q = dict(zip(qv["indices"], qv["values"]))
                for h in solo:
                    d = dict(zip(*_sparse.sparse_vector(h["payload"].get("text", "")).values()))
                    h["score"] = sum(q.get(t, 0.0) * v for t, v in d.items())
                    h["_exact_rank"] = True  # 精準命中，rerank 置頂
                exact = solo[:3]
                exact_ids = {h["id"] for h in exact}
                hits = exact + [h for h in hits if h["id"] not in exact_ids]
                return hits
        # 同條號跨法候選（僅條號查詢）：scroll 全拉（不依賴 dense 排位，避免真身被擠出
        # 小 limit），本地稀疏 dot＋法名 bigram 重疊計分 → prepend top3。
        f2 = {"must": _BASE_CONDITIONS + [{"should": [{"key": "article_no", "match": {"value": an}}]}]}
        r = await gateway._req("qdrant", gateway.QDRANT_URLS, "post",
                       f"/collections/{COLLECTION}/points/scroll",
                       json={"filter": f2, "limit": 1000, "with_payload": True, "with_vector": False},
                       timeout=30)
        r.raise_for_status()
        exact = r.json()["result"]["points"]
        if exact:
            qv = _sparse.sparse_vector(question)
            q = dict(zip(qv["indices"], qv["values"]))
            qbig = cn_parse._bigrams(question)
            for h in exact:
                d = dict(zip(*_sparse.sparse_vector(h["payload"].get("text", "")).values()))
                # dot＝內容/特徵重疊；＋法名 bigram 重疊破「內容不含法名詞彙引致的同分」
                law = h["payload"].get("law_name", "")
                h["_exact"] = sum(q.get(t, 0.0) * v for t, v in d.items()) + 3.0 * len(qbig & cn_parse._bigrams(law))
            exact.sort(key=lambda h: h["_exact"], reverse=True)
            exact = [h for h in exact if h["_exact"] > 0][:3]
            for h in exact:
                h["score"] = h.pop("_exact", 0.0)
                h["_exact_rank"] = True  # 供本地 rerank 保留精準分支的領先順序
        exact_ids = {h["id"] for h in exact}
        # exact 排最前，一併去重（可能已在 fused hit 中段）；top_k 才能看到真身。
        hits = exact + [h for h in hits if h["id"] not in exact_ids]
    else:
        # 法名分支：查詢即法名（例:「證券交易法」）時，dense 前段常被「提及該法名」的其他法
        # 條文佔據，本法條文反而排不進 top；滾出本法條文（條號升序）prepend 當「來源」。
        law = law_meta._detect_law(question)
        if law:
            r = await gateway._req("qdrant", gateway.QDRANT_URLS, "post",
                           f"/collections/{COLLECTION}/points/scroll",
                           json={"filter": {"must": _BASE_CONDITIONS +
                                            [{"key": "law_name", "match": {"value": law}}]},
                                 "limit": 300, "with_payload": True, "with_vector": False},
                           timeout=30)
            r.raise_for_status()
            arts = sorted(r.json()["result"]["points"],
                          key=lambda h: law_meta._art_sort_key(h["payload"].get("article_no", "")))
            top = arts[:3]
            for h in top:
                h["score"] = 0.0          # 穩定排序用（rerank 的精準分支維持輸入順序）
                h["_brief"] = law_meta._law_brief(law)
                h["_exact_rank"] = True   # 視同精準命中（法名精準），rerank 置頂
            lid = {h["id"] for h in top}
            hits = top + [h for h in hits if h["id"] not in lid]
    return hits
async def _dense_leg(vector: list[float], limit: int) -> dict[int, float]:
    """dense 腿單查：回「該 query 在 corpus 的最佳 dense 餘弦」集合，供閘門（絕對值）。
    Qdrant 的 id match any 不接受超過 i64 的 u64 id（md5 id 常超過），故不能對特定 id 回拉。"""
    r = await gateway._req("qdrant", gateway.QDRANT_URLS, "post", f"/collections/{COLLECTION}/points/query",
                   json={"query": vector, "using": "dense", "limit": limit,
                         "with_payload": False}, timeout=30)
    r.raise_for_status()
    return {p["id"]: p["score"] for p in r.json()["result"]["points"]}
def rerank(question: str, hits: list[dict], dense_scores: dict | None = None,
           top_k: int = 5) -> list[dict]:
    """本地重排（純計算）：條號精準分支（_exact_rank）領先；其餘依「真實 dense 語意相似度」降序
    （pool 已附每筆 dense，稀疏僅主導的噪音自然沉底）。_dense 缺時補自頂層 dense leg。"""
    dense = dense_scores or {}
    exact = [h for h in hits if h.get("_exact_rank")]
    rest = sorted((h for h in hits if not h.get("_exact_rank")),
                  key=lambda h: h.get("_dense") if h.get("_dense") is not None else -1.0,
                  reverse=True)
    out = (exact + rest)[:top_k]
    for h in out:
        if h.get("_dense") is None:
            h["_dense"] = dense.get(h["id"])
    return out
# 法律領域提示語彙（寬鬆即可；真正門檻是 dense，此僅決定「弱區間」要不要放行）
_LAW_HINTS = ("法條", "條文", "契約", "債", "侵權", "賠償", "損害", "婚姻", "離婚", "繼承",
              "遺產", "贈與", "買賣", "租", "工資", "勞工", "僱", "雇", "刑", "罪", "罰",
              "訴訟", "起訴", "上訴", "判決", "被害人", "詐欺", "竊盜", "侵占", "偽造",
              "背信", "酒駕", "肇事", "交通", "保險", "稅", "股份有限公司", "董事", "股東",
              "親權", "扶養", "監護", "戶政", "土地", "鄰居", "噪音", "合夥", "委任", "承攬",
              "保證", "被繼承", "特留分", "應繼分", "營業秘密", "定型化契約", "商品責任",
              "特別休假", "資遣費", "退休金", "職災", "工時", "調解", "公證", "執行" )
def _trace(question: str, an: str | None, exact_n: int, dense_max: float,
           level: str, reason: str,
           min_dense: float = MIN_DENSE, mid: float = MID_DENSE,
           high: float = HIGH_DENSE) -> str:
    """流程判定摘要（以「｜」間隔，便於人讀）：條號 → 精準命中 → 語意相似度/門檻 → 語意訊號 → 信心判定。"""
    sig = "有" if _legal_signal(question) else "無"
    return (f"條號:{an or '無'}｜精準:{exact_n}篇｜"
            f"dense:{dense_max:.2f}(門檻{min_dense:.2f}/{mid:.2f}/{high:.2f})｜"
            f"語意:{sig}｜判定:{level}({reason})")
def _legal_signal(question: str) -> bool:
    """整題有無「法律語意」：含條號、含法律語彙即可。"""
    if cn_parse.extract_article_no(question):
        return True
    qq = "".join(question.split())
    if any(k in qq for k in _LAW_HINTS):
        return True
    return False
def _exact_match(hits: list[dict], article_no: str) -> bool:
    """精準分支是否有命中「與條號完全相同」的點（article_no 已去空白比對）。"""
    want = "".join(article_no.split())
    return any(h.get("_exact_rank")
               and "".join((h.get("payload", {}).get("article_no") or "").split()) == want
               for h in hits)
def _decide(question: str, hits: list[dict], dense_max: float,
            min_dense: float = MIN_DENSE, mid: float = MID_DENSE,
            high: float = HIGH_DENSE) -> tuple[str, str]:
    """信心分級（純計算）：corpus 無密合語意（dense_max 太低）→ no_match（不問 LLM）；
    中間區間要「法律語意」才放行。dense_max＝該 query 在 corpus 的最佳 dense 餘弦（跨候選）。
    法名精準命中（例:「證券交易法」及其簡稱）→ 意圖明確、永不放 no_match（來源由法名分支列出）。"""
    if not hits:
        return "no_match", "empty"
    # ⚠️ 全部命中都是廢止／中止條文 → 不該當成「有答案」。
    #   廢止條文改成可以被查到之後（2026-10-05），這裡是最後一道閘門：
    #   語意再像也不該用一條已刪除的條文去產生 high 信心 —— 那會讓
    #   「查不到現行規定」看起來像「有答案、而且很確定」。
    #   medium 而非 no_match：條文本身是真實存在的歷史紀錄，可供對照。
    live = [h for h in hits
            if not ((h.get("payload") or {}).get("is_repealed")
                    or (h.get("payload") or {}).get("is_abandoned"))]
    if not live:
        cos = max(dense_max, 0.0)
        return "medium", f"repealed_only@cos:{cos:.2f}"
    law = law_meta._detect_law(question)
    if law and any((h.get("payload") or {}).get("law_name") == law for h in hits):
        cos = max(dense_max, 0.0)
        return "high", f"law_name@{cos:.2f}"
    cos = max(dense_max, 0.0)
    if cos < min_dense:
        # 條號精準命中不因 dense 偏低被誤判（精準分支本為破「熱門條號被擁擠」而生）
        an = cn_parse.extract_article_no(question)
        if an and _exact_match(hits, an):
            return "medium", f"exact_article@cos:{cos:.2f}"
        return "no_match", f"low_relevance@{cos:.2f}"
    if not _legal_signal(question) and cos < mid:
        return "no_match", f"ambiguous_no_signal@{cos:.2f}"
    if cos >= high:
        return "high", f"cos@{cos:.2f}"
    return "medium", f"cos@{cos:.2f}"
def _ref(h: dict) -> str:
    """把 hit 渲染成可標註的引用：moj 條文有 law_name/article_no；判決/ingest 走 case_no/law。"""
    p = h.get("payload", {})
    if p.get("law_name"):
        chap = f"（{p['chapter']}）" if p.get("chapter") else ""
        st = _law.summarize(p.get("text", ""))
        suffix = f"｜{st}" if st else ""
        return f"[法條:{p['law_name']} {p.get('article_no', '').strip()} {chap}{suffix}]"
    return f"[案號:{p.get('case_no', '?')} 法條:{p.get('law', '?')}]"
