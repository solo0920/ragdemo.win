"""RAG 管線：embed -> Qdrant 召回 -> rerank(預留) -> LLM 生成。

服務位址一律走候選清單（OLLAMA_URLS / QDRANT_URLS），先後順序即優先權：
- 首選「優先權最高且目前可用（TCP＋模型齊備）」者，快取一段時間（PICK_TTL）。
- 連線錯誤 / model 404 自動降級到下一台；PICK_TTL 過期會重新掃描，率先主機回復即自動切回。
- 每台 ollama 主機可配不同 LLM model（OLLAMA_MODELS 與 OLLAMA_URLS 同順序對應）。
- 模型 keepalive：要求常駐（KEEP_ALIVE，預設 -1 永久），api 啟動時 warmup 預載，避免首個 query 冷載入。
"""
import asyncio
import logging
import os
import re
import time
import uuid
from urllib.parse import urlparse

import httpx

from . import sparse as _sparse
from . import law_struct as _law

logger = logging.getLogger("ragdemo")

OLLAMA_DEFAULT = os.getenv("OLLAMA_BASE_URL", "http://100.119.83.111:11434").rstrip("/")
OLLAMA_URLS = [u.strip().rstrip("/") for u in os.getenv("OLLAMA_URLS", OLLAMA_DEFAULT).split(",") if u.strip()] or [OLLAMA_DEFAULT]
QDRANT_DEFAULT = os.getenv("QDRANT_URL", "http://localhost:6333").rstrip("/")
QDRANT_URLS = [u.strip().rstrip("/") for u in os.getenv("QDRANT_URLS", QDRANT_DEFAULT).split(",") if u.strip()] or [QDRANT_DEFAULT]
EMBED_MODEL = os.getenv("EMBED_MODEL", "bge-m3:latest")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3:14b")
# OLLAMA_MODELS：與 OLLAMA_URLS 同順序的 LLM model 清單；未設則全部用 LLM_MODEL。
OLLAMA_MODELS = [m.strip() for m in os.getenv("OLLAMA_MODELS", LLM_MODEL).split(",") if m.strip()] or [LLM_MODEL]
RERANK_MODEL = os.getenv("RERANK_MODEL", "qllama/bge-reranker-v2-m3:latest")
COLLECTION = os.getenv("COLLECTION", "laws")
DIM = 1024  # bge-m3 向量維度
HOST_ID = os.getenv("HOST_ID", "x570")
# 重新掃描優先權的間隔（秒）：降級後每 PICK_TTL 重測一次，高位主機回復就切回。
PICK_TTL = float(os.getenv("PICK_TTL", "30"))
# 模型常駐時間（ollama keep_alive）：-1=永久常駐（預設）、0=即時卸載、"30m"=30 分鐘。
KEEP_ALIVE = os.getenv("KEEP_ALIVE", "-1")
# 前端「連線與來源」彈窗的主機探測（與 Pages worker 同語意：公網 api /health）。
# dev（vite proxy 直連 backend、繞過 worker）由 backend 補 log；prod 的 worker 會自行覆蓋此欄位。
HOST_API = {
    "x570": os.getenv("HOST_API_X570", "https://api-x570.ragdemo.win").rstrip("/"),
    "mbp": os.getenv("HOST_API_MBP", "https://api-mbp.ragdemo.win").rstrip("/"),
    "msi": os.getenv("HOST_API_MSI", "https://api-msi.ragdemo.win").rstrip("/"),
}
PROBE_TIMEOUT = float(os.getenv("PROBE_TIMEOUT", "2.5"))


def keep_alive_value():
    """ollama 的 keep_alive：純數字（含 -1）要傳 number，其餘（如 "30m"）傳字串。"""
    s = str(KEEP_ALIVE).strip()
    return int(s) if s.lstrip("-").isdigit() else s

SYSTEM = "你是法規判決檢索助理。只依據提供的資料回答，並標註案號/條號；找不到就說找不到，不要編造。回答某條時，除主旨外若該條含款/項，請說明其下共幾項、幾款並摘要各款要旨。"

_bases: dict[str, str] = {}
_base_ts: dict[str, float] = {}
_base_lock = asyncio.Lock()


async def _tcp_open(url: str, timeout: float = 2.0) -> bool:
    p = urlparse(url)
    port = p.port or (443 if p.scheme == "https" else 80)
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(p.hostname, port, ssl=p.scheme == "https"), timeout
        )
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


def _llm_model_for(url: str) -> str:
    """該 ollama 主機對應的 LLM model（OLLAMA_MODELS 與 OLLAMA_URLS 同順序）。"""
    try:
        i = OLLAMA_URLS.index(url)
        return OLLAMA_MODELS[i] if i < len(OLLAMA_MODELS) else LLM_MODEL
    except ValueError:
        return LLM_MODEL


def active_llm_source() -> str:
    """目前快取的 ollama 主機與其 model（未 pick 前回本機宣告）。"""
    base = _bases.get("ollama")
    if not base:
        return f"{OLLAMA_URLS[0]} -> {_llm_model_for(OLLAMA_URLS[0])}"
    return f"{base} -> {_llm_model_for(base)}"


# 已知三機 tailscale IP → 主機 id（供來源標註用）；127.0.0.1/localhost 視為本機。
_KNOWN_IPS = {"100.119.83.111": "x570", "100.64.121.9": "mbp", "100.65.68.106": "msi"}


def host_label(url: str) -> str:
    """把服務 URL 映射成主機 id（無法辨識則回本機宣告）。"""
    host = (urlparse(url).hostname or "").lower()
    if host in ("127.0.0.1", "localhost"):
        return HOST_ID
    for ip, name in _KNOWN_IPS.items():
        if ip in host:
            return name
    if "." not in host:  # compose service 名（如 qdrant）在本機跑 → 標本機
        return HOST_ID
    return host


async def _host_probe_log() -> dict[str, str]:
    """並行探測三台公網 api /health，回傳前端「連線與來源」的 log（與 worker 同字串）。
    worker 在 prod 會用自己探的 log 覆蓋；此函式主要服務 dev（vite proxy 直連 backend）。"""
    async def _one(url: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=PROBE_TIMEOUT, follow_redirects=True) as c:
                r = await c.get(f"{url}/health")
                return r.status_code < 500
        except Exception:
            return False
    ok = await asyncio.gather(*(_one(u) for u in HOST_API.values()))
    return {hid: ("連線成功" if o else "連線失敗") for hid, o in zip(HOST_API, ok)}


async def _ollama_probe(url: str) -> bool:
    """ollama 主機可用：TCP 通，且同時具備該機對應的 LLM model 與 EMBED_MODEL（避免 404）。"""
    if not await _tcp_open(url):
        return False
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{url}/api/tags")
        r.raise_for_status()
        have = {m["name"] for m in r.json().get("models", [])}
        need = {_llm_model_for(url), EMBED_MODEL}
        return need <= have
    except Exception:
        return False


async def _pick(kind: str, candidates: list[str], probe=None) -> str:
    """依優先權選取可用主機：快取新鮮（PICK_TTL 內）直接用；過期重新掃描，
    先位主機回復即自動切回。全部不通時退回候選首位（由 _req 依錯誤降級）。"""
    now = time.monotonic()
    cur = _bases.get(kind)
    if cur and now - _base_ts.get(kind, 0) < PICK_TTL:
        return cur
    async with _base_lock:
        cur = _bases.get(kind)
        if cur and now - _base_ts.get(kind, 0) < PICK_TTL:
            return cur
        for url in candidates:
            ok = await _tcp_open(url)
            if ok and probe is not None:
                ok = await probe(url)
            if ok:
                _bases[kind] = url
                _base_ts[kind] = now
                return url
    _bases[kind] = candidates[0]
    _base_ts[kind] = now
    return candidates[0]


def _drop(kind: str) -> None:
    _bases.pop(kind, None)
    _base_ts.pop(kind, None)


async def _req(kind: str, candidates: list[str], method: str, path: str,
               *, timeout: float = 120, retry_on: tuple = (), **kw) -> httpx.Response:
    probe = _ollama_probe if kind == "ollama" else None
    for _ in range(2):
        base = await _pick(kind, candidates, probe=probe)
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await getattr(c, method)(f"{base}{path}", **kw)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            _drop(kind)
            continue
        if r.status_code in retry_on:
            _drop(kind)  # 例如 404 model not found → 換下一台
            continue
        return r
    raise httpx.ConnectError(f"{kind} unreachable")


async def embed(texts: list[str]) -> list[list[float]]:
    r = await _req("ollama", OLLAMA_URLS, "post", "/api/embed",
                   json={"model": EMBED_MODEL, "input": texts, "keep_alive": keep_alive_value()},
                   retry_on=(404,))
    r.raise_for_status()
    return r.json()["embeddings"]


# collection 能力偵測：有 sparse 命名向量 → 走 hybrid(DBSF)；單一未命名 dense → 舊 search。
HAS_SPARSE = False
_HAS_NAMED = False
# hybrid 查詢預設過濾：只找人吃得到的現行條文（demo/ingest 或 backup 舊點會被排除）
_BASE_FILTER = {"must": [
    {"key": "is_repealed", "match": {"value": False}},
    {"key": "is_abandoned", "match": {"value": False}},
]}


async def _collection_capabilities() -> None:
    """讀 collection 設定，記錄 HAS_SPARSE/_HAS_NAMED（供 search 選路）。"""
    global HAS_SPARSE, _HAS_NAMED
    try:
        r = await _req("qdrant", QDRANT_URLS, "get", f"/collections/{COLLECTION}", timeout=30)
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
    r = await _req("qdrant", QDRANT_URLS, "get", f"/collections/{COLLECTION}", timeout=30)
    if r.status_code == 200:
        return
    r = await _req("qdrant", QDRANT_URLS, "put", f"/collections/{COLLECTION}", timeout=30,
                   json={"vectors": {"dense": {"size": DIM, "distance": "Cosine"}},
                         "sparse_vectors": {"sparse": {"modifier": "idf"}}})
    r.raise_for_status()
    await _collection_capabilities()


async def upsert(docs: list[dict]) -> int:
    vecs = await embed([d["text"] for d in docs])
    points = [
        {"id": str(uuid.uuid4()),
         "vector": {"dense": v} | ({"sparse": _sparse.sparse_vector(d["text"])} if HAS_SPARSE else {}),
         "payload": d}
        for v, d in zip(vecs, docs)
    ]
    r = await _req("qdrant", QDRANT_URLS, "put", f"/collections/{COLLECTION}/points",
                   json={"points": points})
    r.raise_for_status()
    return len(points)


async def search(question: str, vector: list[float], limit: int = 50) -> list[dict]:
    """召回：hybrid 用 DBSF 分數融合（dense bge-m3＋sparse TF，頂層 filter 只吃現行條文）。
    不用 RRF：RRF 只看排名，熱門條號(如「第11條」)兩腿都被灌滿時，真身(e.g. 證交法11)
    在 sparse 腿排到上百名，被融合丟掉；DBSF 正規化分數加總，同時命中兩組 token 的文件會勝出。"""
    if HAS_SPARSE:
        # 每腿多抓些候選：熱門條號(如「第11條」)在 sparse 腿可排到上百名，
        # 只取 top-limit 會把真身丟出融合。DBSF 取 top limit 做最終融合。
        prefetch = max(limit, 500)
        body = {
            "prefetch": [
                {"query": vector, "using": "dense", "limit": prefetch, "filter": _BASE_FILTER},
                {"query": _sparse.sparse_vector(question), "using": "sparse", "limit": prefetch,
                 "filter": _BASE_FILTER},
            ],
            "query": {"fusion": "dbsf"},
            "limit": limit,
            "with_payload": True,
        }
        r = await _req("qdrant", QDRANT_URLS, "post", f"/collections/{COLLECTION}/points/query",
                       json=body, timeout=30)
        r.raise_for_status()
        hits = r.json()["result"]["points"]
        # 條號精準分支：query 含「第N條」時，同時對「同條號、跨法」候選以 dense 打分，
        # 破除熱門條號被擁擠（例「證券交易法第20條」sparse 腿排到數百名外）與"湊巧含法名子串"
        # 的文件搶位的問題；法名+條號組合下真正的條文會衝到最前。
        an = extract_article_no(question)
        if an:
            # 同條號跨法候選：scroll 全拉（不依賴 dense 排位，避免真身被擠出小 limit），
            # 本地稀疏 dot＋法名 bigram 重疊計分 → prepend top3。
            f2 = {"must": _BASE_FILTER["must"] + [{"should": [{"key": "article_no", "match": {"value": an}}]}]}
            r = await _req("qdrant", QDRANT_URLS, "post",
                           f"/collections/{COLLECTION}/points/scroll",
                           json={"filter": f2, "limit": 1000, "with_payload": True, "with_vector": False},
                           timeout=30)
            r.raise_for_status()
            exact = r.json()["result"]["points"]
            if exact:
                qv = _sparse.sparse_vector(question)
                q = dict(zip(qv["indices"], qv["values"]))
                qbig = _bigrams(question)
                for h in exact:
                    d = dict(zip(*_sparse.sparse_vector(h["payload"].get("text", "")).values()))
                    # dot＝內容/特徵重疊；＋法名 bigram 重疊破「內容不含法名詞彙引致的同分」
                    law = h["payload"].get("law_name", "")
                    h["_exact"] = sum(q.get(t, 0.0) * v for t, v in d.items()) + 3.0 * len(qbig & _bigrams(law))
                exact.sort(key=lambda h: h["_exact"], reverse=True)
                exact = [h for h in exact if h["_exact"] > 0][:3]
                for h in exact:
                    h["score"] = h.pop("_exact", 0.0)
            exact_ids = {h["id"] for h in exact}
            # exact 排最前，一併去重（可能已在 hybrid hit 中段）；top_k 才能看到真身。
            hits = exact[:3] + [h for h in hits if h["id"] not in exact_ids]
        return hits
    if _HAS_NAMED:
        body = {"vector": {"dense": vector}, "limit": limit, "with_payload": True}
    else:
        body = {"vector": vector, "limit": limit, "with_payload": True}
    r = await _req("qdrant", QDRANT_URLS, "post", f"/collections/{COLLECTION}/points/search",
                   json=body, timeout=30)
    r.raise_for_status()
    return r.json()["result"]


def rerank(question: str, hits: list[dict], top_k: int = 5) -> list[dict]:
    # TODO: reranker 需走 chat/generate 逐對打分，目前先取向量分數前 top_k，
    # 模型 RERANK_MODEL 已備好，待實作後替換此函式。
    return hits[:top_k]


_CN_DIG = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _cn2num(s: str) -> int:
    """中文數字→int（支援至千位）：十一=11、二十三=23、一百零五=105、兩百=200。"""
    s = s.replace("兩", "二").replace("零", "")
    total = cur = 0
    for ch in s:
        if ch == "千":
            total += (cur or 1) * 1000; cur = 0
        elif ch == "百":
            total += (cur or 1) * 100; cur = 0
        elif ch == "十":
            total += (cur or 1) * 10; cur = 0
        elif ch in _CN_DIG:
            cur = cur * 10 + _CN_DIG[ch]
    return total + cur


_ART_RE = re.compile(
    r"第\s*(?:(?P<ab>[0-9]+(?:\s*-\s*[0-9]+)?)|(?P<cn>[一二三四五六七八九十百零兩]+(?:之[一二三四五六七八九十零兩]+)?))\s*條"
)


def _bigrams(s: str) -> set[str]:
    """字串的 CJK bigram 集合（去掉空白），用以比對法名與 query 的重疊。"""
    s = "".join(s.split())
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else set()


def extract_article_no(question: str) -> str | None:
    """從查詢抽出「條」的標準格式（例:「第20條」→「第 20 條」、「第10條之1」→「第 10-1 條」）。"""
    m = _ART_RE.search(question)
    if not m:
        return None
    if m.group("cn"):
        raw = m.group("cn")
        if "之" in raw:
            head, tail = raw.split("之", 1)
            s = f"{_cn2num(head)}-{_cn2num(tail)}"
        else:
            s = str(_cn2num(raw))
    else:
        s = m.group("ab").strip()
    return f"第 {s.strip()} 條"


def _ref(h: dict) -> str:
    """把 hit 渲染成可標註的引用：moj 條文有 law_name/article_no；判決/ingest 走 case_no/law。"""
    p = h.get("payload", {})
    if p.get("law_name"):
        chap = f"（{p['chapter']}）" if p.get("chapter") else ""
        st = _law.summarize(p.get("text", ""))
        suffix = f"｜{st}" if st else ""
        return f"[法條:{p['law_name']} {p.get('article_no', '').strip()} {chap}{suffix}]"
    return f"[案號:{p.get('case_no', '?')} 法條:{p.get('law', '?')}]"


async def generate(question: str, contexts: list[dict]) -> str:
    blocks = "\n\n".join(f"{_ref(h)} {h['payload'].get('text', '')}" for h in contexts)
    prompt = f"{SYSTEM}\n\n資料：\n{blocks}\n\n問題：{question}\n回答（附案號/條號）："
    # model 依選中的 ollama 主機而定（OLLAMA_MODELS 同序對應）：x570/mbp=qwen3:14b、msi=qwen3:8b。
    # 連線錯誤 / 404(model not found) 降級下一台；迴圈可走遍所有候選。
    for _ in range(len(OLLAMA_URLS) + 1):
        base = await _pick("ollama", OLLAMA_URLS, probe=_ollama_probe)
        model = _llm_model_for(base)
        try:
            async with httpx.AsyncClient(timeout=300) as c:
                r = await c.post(f"{base}/api/generate",
                                 json={"model": model, "prompt": prompt, "stream": False,
                                       "think": False, "keep_alive": keep_alive_value(),
                                       "options": {"num_predict": 500}})
        except (httpx.ConnectError, httpx.ConnectTimeout):
            _drop("ollama")
            continue
        if r.status_code == 404:
            _drop("ollama")  # 該機沒有此 model → 換下一台
            continue
        r.raise_for_status()
        return r.json()["response"]
    raise httpx.ConnectError("ollama unreachable")


async def local_models() -> list[str]:
    """問本機 ollama 持有的模型清單（供 registry 記錄），失敗回空。"""
    try:
        r = await _req("ollama", OLLAMA_URLS, "get", "/api/tags", timeout=10)
        return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return []


async def warmup() -> None:
    """啟動時預載「選中主機」的預設 LLM 與 embedding 模型並常駐（keep_alive=KEEP_ALIVE）。

    best-effort：ollama 未就緒就跳過，首個 query 再載；萬一失敗不影響 api 上線。
    """
    try:
        base = await _pick("ollama", OLLAMA_URLS, probe=_ollama_probe)
    except Exception:
        return
    try:
        async with httpx.AsyncClient(timeout=300) as c:
            results = await asyncio.gather(
                c.post(f"{base}/api/generate",
                       json={"model": _llm_model_for(base), "prompt": "", "stream": False,
                             "think": False, "keep_alive": keep_alive_value(),
                             "options": {"num_predict": 1}}),
                c.post(f"{base}/api/embed",
                       json={"model": EMBED_MODEL, "input": "", "keep_alive": keep_alive_value()}),
                return_exceptions=True,
            )
        for r in results:
            if isinstance(r, Exception) or (hasattr(r, "status_code") and r.status_code >= 400):
                logger.warning("warmup 部分失敗（%s），將在首個 query 載入", r)
                return
        logger.info("warmup 完成：%s 常駐 %s ＋ %s", base, _llm_model_for(base), EMBED_MODEL)
    except Exception as e:
        logger.warning("warmup 失敗（%s），將在首個 query 載入", e)


async def answer(question: str, recall: int = 50, top_k: int = 5) -> dict:
    vecs = await embed([question])
    hits = await search(question, vecs[0], limit=recall)
    top = rerank(question, hits, top_k)
    text = await generate(question, top)
    text = f"{HOST_ID}: {text}"
    src = {
        "qdrant": {"host": host_label(_bases.get("qdrant", QDRANT_URLS[0])),
                   "url": _bases.get("qdrant", QDRANT_URLS[0])},
        "llm": {"host": host_label(_bases.get("ollama", OLLAMA_URLS[0])),
                "url": _bases.get("ollama", OLLAMA_URLS[0]),
                "model": _llm_model_for(_bases.get("ollama", OLLAMA_URLS[0]))},
    }
    return {"ok": True, "answer": text, "src": src, "hits": [_hit_view(h) for h in top],
            "host": HOST_ID, "log": await _host_probe_log()}


def _hit_view(h: dict) -> dict:
    """前端引用渲染用的精簡欄位：機率／條號／款位／內容（保留 payload 供既有 UI）。"""
    p = h.get("payload", {})
    view = {"score": h["score"], "payload": p,
            "art": (p.get("article_no") or "").replace(" ", "") or p.get("law", ""),
            "law_name": p.get("law_name", ""),
            "item": _law.cite_item(p.get("text", ""))}
    s = _law.structure(p.get("text", ""))
    view["para_count"] = s["para"]
    view["item_count"] = len(s["items"])
    return view