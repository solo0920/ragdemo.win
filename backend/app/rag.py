"""RAG 管線：embed -> Qdrant 召回 -> rerank(預留) -> LLM 生成。

服務位址一律走候選清單（OLLAMA_URLS / QDRANT_URLS），先後順序即優先權：
- 首選「優先權最高且目前可用（TCP＋模型齊備）」者，快取一段時間（PICK_TTL）。
- 連線錯誤 / model 404 自動降級到下一台；PICK_TTL 過期會重新掃描，率先主機回復即自動切回。
- 每台 ollama 主機可配不同 LLM model（OLLAMA_MODELS 與 OLLAMA_URLS 同順序對應）。
"""
import asyncio
import os
import time
import uuid
from urllib.parse import urlparse

import httpx

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

SYSTEM = "你是法規判決檢索助理。只依據提供的資料回答，並標註案號/條號；找不到就說找不到，不要編造。"

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
                   json={"model": EMBED_MODEL, "input": texts}, retry_on=(404,))
    r.raise_for_status()
    return r.json()["embeddings"]


async def ensure_collection() -> None:
    r = await _req("qdrant", QDRANT_URLS, "get", f"/collections/{COLLECTION}", timeout=30)
    if r.status_code == 200:
        return
    r = await _req("qdrant", QDRANT_URLS, "put", f"/collections/{COLLECTION}", timeout=30,
                   json={"vectors": {"size": DIM, "distance": "Cosine"}})
    r.raise_for_status()


async def upsert(docs: list[dict]) -> int:
    vecs = await embed([d["text"] for d in docs])
    points = [
        {"id": str(uuid.uuid4()), "vector": v, "payload": d}
        for v, d in zip(vecs, docs)
    ]
    r = await _req("qdrant", QDRANT_URLS, "put", f"/collections/{COLLECTION}/points",
                   json={"points": points})
    r.raise_for_status()
    return len(points)


async def search(vector: list[float], limit: int = 50) -> list[dict]:
    r = await _req("qdrant", QDRANT_URLS, "post", f"/collections/{COLLECTION}/points/search",
                   json={"vector": vector, "limit": limit, "with_payload": True})
    r.raise_for_status()
    return r.json()["result"]


def rerank(question: str, hits: list[dict], top_k: int = 5) -> list[dict]:
    # TODO: reranker 需走 chat/generate 逐對打分，目前先取向量分數前 top_k，
    # 模型 RERANK_MODEL 已備好，待實作後替換此函式。
    return hits[:top_k]


async def generate(question: str, contexts: list[dict]) -> str:
    blocks = "\n\n".join(
        f"[案號:{h['payload'].get('case_no', '?')} 法條:{h['payload'].get('law', '?')}] {h['payload'].get('text', '')}"
        for h in contexts
    )
    prompt = f"{SYSTEM}\n\n資料：\n{blocks}\n\n問題：{question}\n回答（附案號/條號）："
    # model 依選中的 ollama 主機而定（OLLAMA_MODELS 同序對應）：x570/mbp=qwen3:14b、msi=qwen3:4b。
    # 連線錯誤 / 404(model not found) 降級下一台；迴圈可走遍所有候選。
    for _ in range(len(OLLAMA_URLS) + 1):
        base = await _pick("ollama", OLLAMA_URLS, probe=_ollama_probe)
        model = _llm_model_for(base)
        try:
            async with httpx.AsyncClient(timeout=300) as c:
                r = await c.post(f"{base}/api/generate",
                                 json={"model": model, "prompt": prompt, "stream": False,
                                       "think": False, "options": {"num_predict": 500}})
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


async def answer(question: str, recall: int = 50, top_k: int = 5) -> dict:
    vecs = await embed([question])
    hits = await search(vecs[0], limit=recall)
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
    return {"answer": text, "src": src, "hits": [
        {"score": h["score"], "payload": h["payload"]} for h in top]}