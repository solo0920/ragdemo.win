"""FastAPI：/health /ingest /query /eval，模型與服務全走環境變數。"""
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import rag


@asynccontextmanager
async def lifespan(app: FastAPI):
    await rag.ensure_collection()
    yield


app = FastAPI(title="RagDemo API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class Doc(BaseModel):
    text: str
    case_no: str = ""
    law: str = ""
    date: str = ""


class Query(BaseModel):
    question: str
    recall: int = 50
    top_k: int = 5


@app.get("/health")
async def health():
    return {"ok": True, "collection": rag.COLLECTION, "llm": rag.LLM_MODEL}


@app.post("/ingest")
async def ingest(docs: list[Doc]):
    n = await rag.upsert([d.model_dump() for d in docs])
    return {"ingested": n, "collection": rag.COLLECTION}


@app.post("/query")
async def query(q: Query):
    return await rag.answer(q.question, q.recall, q.top_k)


@app.post("/eval")
async def evaluate():
    """跑 evals/questions.json，回報引註命中率。"""
    path = Path(__file__).resolve().parents[2] / "evals" / "questions.json"
    items = json.loads(path.read_text(encoding="utf-8"))
    hit = 0
    tested = 0
    for it in items:
        if not it.get("expect_case"):
            continue  # 範例佔位題跳過
        tested += 1
        res = await rag.answer(it["q"], recall=50, top_k=5)
        cases = [h["payload"].get("case_no", "") for h in res["hits"]]
        if any(it["expect_case"] in c for c in cases):
            hit += 1
    return {"tested": tested, "hit": hit,
            "hit_rate": round(hit / tested, 3) if tested else 0.0}
