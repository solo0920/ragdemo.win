"""RAG 管線：embed -> Qdrant 召回 -> rerank(預留) -> LLM 生成，全走環境變數。"""
import os
import uuid

import httpx

OLLAMA = os.getenv("OLLAMA_BASE_URL", "http://192.168.0.99:11434").rstrip("/")
QDRANT = os.getenv("QDRANT_URL", "http://localhost:6333").rstrip("/")
EMBED_MODEL = os.getenv("EMBED_MODEL", "bge-m3:latest")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3:14b")
RERANK_MODEL = os.getenv("RERANK_MODEL", "qllama/bge-reranker-v2-m3:latest")
COLLECTION = os.getenv("COLLECTION", "laws")
DIM = 1024  # bge-m3 向量維度

SYSTEM = "你是法規判決檢索助理。只依據提供的資料回答，並標註案號/條號；找不到就說找不到，不要編造。"


async def embed(texts: list[str]) -> list[list[float]]:
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"{OLLAMA}/api/embed", json={"model": EMBED_MODEL, "input": texts})
        r.raise_for_status()
        return r.json()["embeddings"]


async def ensure_collection() -> None:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{QDRANT}/collections/{COLLECTION}")
        if r.status_code == 200:
            return
        r = await c.put(
            f"{QDRANT}/collections/{COLLECTION}",
            json={"vectors": {"size": DIM, "distance": "Cosine"}},
        )
        r.raise_for_status()


async def upsert(docs: list[dict]) -> int:
    vecs = await embed([d["text"] for d in docs])
    points = [
        {"id": str(uuid.uuid4()), "vector": v, "payload": d}
        for v, d in zip(vecs, docs)
    ]
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.put(f"{QDRANT}/collections/{COLLECTION}/points", json={"points": points})
        r.raise_for_status()
    return len(points)


async def search(vector: list[float], limit: int = 50) -> list[dict]:
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(
            f"{QDRANT}/collections/{COLLECTION}/points/search",
            json={"vector": vector, "limit": limit, "with_payload": True},
        )
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
    async with httpx.AsyncClient(timeout=300) as c:
        r = await c.post(
            f"{OLLAMA}/api/generate",
            json={"model": LLM_MODEL, "prompt": prompt, "stream": False,
                  "options": {"num_predict": 500}},
        )
        r.raise_for_status()
        return r.json()["response"]


async def answer(question: str, recall: int = 50, top_k: int = 5) -> dict:
    vecs = await embed([question])
    hits = await search(vecs[0], limit=recall)
    top = rerank(question, hits, top_k)
    text = await generate(question, top)
    return {"answer": text, "hits": [
        {"score": h["score"], "payload": h["payload"]} for h in top]}
