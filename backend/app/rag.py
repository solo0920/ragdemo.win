"""RAG 管線：embed -> Qdrant 召回 -> rerank(預留) -> LLM 生成。

服務位址一律走候選清單（OLLAMA_URLS / QDRANT_URLS），先後順序即優先權：
首選通連者被快取，後續發生連線錯誤自動降級到下一個候選。
"""
import asyncio
import os
import uuid
from urllib.parse import urlparse

import httpx

OLLAMA_DEFAULT = os.getenv("OLLAMA_BASE_URL", "http://100.119.83.111:11434").rstrip("/")
OLLAMA_URLS = [u.strip().rstrip("/") for u in os.getenv("OLLAMA_URLS", OLLAMA_DEFAULT).split(",") if u.strip()] or [OLLAMA_DEFAULT]
QDRANT_DEFAULT = os.getenv("QDRANT_URL", "http://localhost:6333").rstrip("/")
QDRANT_URLS = [u.strip().rstrip("/") for u in os.getenv("QDRANT_URLS", QDRANT_DEFAULT).split(",") if u.strip()] or [QDRANT_DEFAULT]
EMBED_MODEL = os.getenv("EMBED_MODEL", "bge-m3:latest")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3:14b")
RERANK_MODEL = os.getenv("RERANK_MODEL", "qllama/bge-reranker-v2-m3:latest")
COLLECTION = os.getenv("COLLECTION", "laws")
DIM = 1024  # bge-m3 向量維度

SYSTEM = "你是法規判決檢索助理。只依據提供的資料回答，並標註案號/條號；找不到就說找不到，不要編造。"

_bases: dict[str, str] = {}
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


async def _pick(kind: str, candidates: list[str]) -> str:
    if kind in _bases:
        return _bases[kind]
    async with _base_lock:
        if kind in _bases:
            return _bases[kind]
        for url in candidates:
            if await _tcp_open(url):
                _bases[kind] = url
                return url
    _bases[kind] = candidates[0]
    return candidates[0]


def _drop(kind: str) -> None:
    _bases.pop(kind, None)


async def _req(kind: str, candidates: list[str], method: str, path: str,
               *, timeout: float = 120, **kw) -> httpx.Response:
    for _ in range(2):
        base = await _pick(kind, candidates)
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                return await getattr(c, method)(f"{base}{path}", **kw)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            _drop(kind)
    raise httpx.ConnectError(f"{kind} unreachable")


async def embed(texts: list[str]) -> list[list[float]]:
    r = await _req("ollama", OLLAMA_URLS, "post", "/api/embed",
                   json={"model": EMBED_MODEL, "input": texts})
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
    r = await _req("ollama", OLLAMA_URLS, "post", "/api/generate",
                   json={"model": LLM_MODEL, "prompt": prompt, "stream": False,
                         "think": False, "options": {"num_predict": 500}},
                   timeout=300)
    r.raise_for_status()
    return r.json()["response"]


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
    return {"answer": text, "hits": [
        {"score": h["score"], "payload": h["payload"]} for h in top]}