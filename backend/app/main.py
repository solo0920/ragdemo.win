"""FastAPI：/health /ingest /query /eval /hosts，模型與服務全走環境變數。"""
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import rag, registry

logger = logging.getLogger("ragdemo")


async def heartbeat_loop():
    while True:
        try:
            await registry.heartbeat(await rag.local_models(), rag.LLM_MODEL)
        except Exception as e:
            logger.warning("heartbeat skipped: %s", e)
        await asyncio.sleep(registry.HEARTBEAT)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await rag.ensure_collection()
    loop = asyncio.get_running_loop()
    loop.create_task(rag.warmup())  # 預載預設模型並常駐，避免首個 query 冷載入
    task = loop.create_task(heartbeat_loop())
    yield
    task.cancel()
    await registry.close()


app = FastAPI(title="RagDemo API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
    return {
        "ok": True,
        "collection": rag.COLLECTION,
        "llm": rag.LLM_MODEL,
        "llm_src": rag.active_llm_source(),
        "host_id": registry.HOST_ID,
        "hostname": registry._my_hostname(),
        "machine_id": registry._system_id(),
        "ips": registry._ips(),
    }


@app.get("/hosts")
async def hosts():
    return {"hosts": await registry.list_hosts()}


@app.post("/ingest")
async def ingest(docs: list[Doc]):
    n = await rag.upsert([d.model_dump() for d in docs])
    return {"ingested": n, "collection": rag.COLLECTION}


@app.post("/query")
async def query(q: Query):
    return await rag.answer(q.question, q.recall, q.top_k)


@app.post("/eval")
async def evaluate():
    """跑 evals/questions.json，回報引註命中率（expect_law 比對「法規名 條號」；expect_case 走路案號）。"""
    base = Path(__file__).resolve().parents  # [1]=/app(container 內 evals 掛載), [2]=repo 根(本機)
    path = base[1] / "evals" / "questions.json"
    if not path.exists():
        path = base[2] / "evals" / "questions.json"
    items = json.loads(path.read_text(encoding="utf-8"))
    hit = 0
    tested = 0
    for it in items:
        if not (it.get("expect_law") or it.get("expect_case")):
            continue  # 佔位題跳過
        tested += 1
        res = await rag.answer(it["q"], recall=50, top_k=5)
        ok = False
        for h in res["hits"]:
            p = h["payload"]
            law = f"{p.get('law_name', '')} {p.get('article_no', '')}"
            if it.get("expect_law") and it["expect_law"] in law:
                ok = True
                break
            if it.get("expect_case") and it["expect_case"] in p.get("case_no", ""):
                ok = True
                break
        if ok:
            hit += 1
    return {"tested": tested, "hit": hit,
            "hit_rate": round(hit / tested, 3) if tested else 0.0}
