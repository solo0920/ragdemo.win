"""FastAPI：/health /ingest /query /eval /hosts，模型與服務全走環境變數。"""
import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import httpx

from . import rag, registry, rules_store as rules
from . import usage

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
    rules.init()  # 題庫預載進記憶體（後續 mtime 自動同步）
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
    model: str = ""  # 指定模型（openrouter/… 走 CF gateway 閉源；其餘 ollama 本機）


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


@app.get("/models")
async def models():
    """可用 LLM 模型清單（首頁下拉選單用）：local＝地端 ollama（隱私）、cloud＝OpenRouter 閉源（速度）、
    zen＝OpenCode Zen free（需 ZEN_API_KEY）。"""
    try:
        local = await rag.local_models()
    except Exception:
        local = []
    local = [m for m in local if ":embed" not in m and "reranker" not in m.lower()]
    return {
        "local": local,
        "cloud": [f"openrouter/{m}" for m in rag.OPENROUTER_MODELS],
        "gateway": bool(rag.OPENROUTER_GATEWAY_URL and rag._gateway_token()),
        "zen": [f"zen/{m}" for m in rag.ZEN_FREE_MODELS],
        "zen_ready": bool(rag.ZEN_API_KEY),
        "nvidia": [f"nv/{m}" for m in rag.NVIDIA_MODELS],
        "nvidia_ready": bool(rag.NVIDIA_API_KEY),
        "gemini": [f"gemini/{m}" for m in rag.GEMINI_MODELS],
        "gemini_ready": bool(rag.GEMINI_GATEWAY_URL and rag.GEMINI_GATEWAY_URL != "-" and rag._gateway_token()),
        "groq": [f"groq/{m}" for m in rag.GROQ_MODELS],
        "groq_ready": bool(rag.GROQ_GATEWAY_URL and rag.GROQ_GATEWAY_URL != "-" and rag._gateway_token()),
        "cohere": [f"cohere/{m}" for m in rag.COHERE_MODELS],
        "cohere_ready": bool(rag.COHERE_GATEWAY_URL and rag.COHERE_GATEWAY_URL != "-" and rag._gateway_token()),
        "hf": [f"hf/{m}" for m in rag.HF_MODELS],
        "hf_ready": bool(rag.HF_BASE_URL and rag.HF_TOKEN),
        "mistral": [f"mis/{m}" for m in rag.MISTRAL_MODELS],
        "mistral_ready": bool(rag.MISTRAL_GATEWAY_URL and rag.MISTRAL_GATEWAY_URL != "-" and rag._gateway_token()),
        "usage": await usage.snapshot(),
        "limited": rag._limited_snapshot(),
        "quota": rag.FREE_QUOTA,
    }


@app.get("/hosts")
async def hosts():
    return {"hosts": await registry.list_hosts()}


@app.get("/status")
async def status():
    """三台主機連線探測（連線詳細彈窗用；不依賴 query，供按鈕常駐顯示）。"""
    return {"ok": True, "host": registry.HOST_ID, "log": await rag._host_probe_log()}


@app.post("/ingest")
async def ingest(docs: list[Doc]):
    n = await rag.upsert([d.model_dump() for d in docs])
    return {"ingested": n, "collection": rag.COLLECTION}


@app.post("/query")
async def query(q: Query):
    try:
        return await rag.answer(q.question, q.recall, q.top_k, model=q.model)
    except httpx.HTTPStatusError as e:
        # 別再一律標成「LLM 上游」：檢索(qdrant)、嵌入(ollama)、gateway 全都丟同一個
        # HTTPStatusError。標錯會把 qdrant 的 400 顯示成「LLM 故障」，排查時被帶去錯的方向
        # （2026-09-26 實測踩過）。改用實際請求的 host。
        host = e.response.request.url.host
        return JSONResponse(
            status_code=e.response.status_code,
            content={"ok": False,
                     "detail": f"上游（{host}）回 {e.response.status_code}：{e.response.text[:200]}"},
        )
    except rag.GatewayUnconfigured as e:
        return JSONResponse(status_code=503, content={"ok": False, "detail": str(e)})


@app.get("/rules")
async def rules_list():
    """題庫頁：內建 intent 目錄＋使用者自定規則（讀取不需 token）。"""
    _ = rules
    return {
        "builtins": rag.builtin_catalog(),
        "user": rules.load(),
        "counts": rules.counts(),
        "source_file": str(rules._pick()),
    }


def _require_admin(authorization: str | None) -> None:
    token = os.getenv("ADMIN_TOKEN", "").strip()
    if not token:
        raise HTTPException(status_code=403, detail="本主機未設定 ADMIN_TOKEN，題庫寫入停用")
    if authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="管理權限不符")


class RuleIn(BaseModel):
    kind: str = "contains"
    match: str
    law: str = ""
    answer: str
    note: str = ""
    enabled: bool = True


@app.post("/rules")
async def rules_add(body: RuleIn, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    r = rules.add(body.model_dump())
    if not r or "error" in r:
        raise HTTPException(status_code=400, detail=(r or {}).get("error", "新增失敗"))
    return r["rule"]


@app.post("/rules/{rid}/toggle")
async def rules_toggle(rid: str, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    r = rules.toggle(rid)
    if not r or "error" in r:
        raise HTTPException(status_code=404, detail=(r or {}).get("error", "操作失敗"))
    return r["rule"]


@app.post("/rules/{rid}/delete")
async def rules_delete(rid: str, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    r = rules.delete(rid)
    if not r or "error" in r:
        raise HTTPException(status_code=404, detail=(r or {}).get("error", "操作失敗"))
    return r


@app.post("/eval")
async def evaluate():
    """跑 evals/questions.json，回報引註命中率（expect_law 比對「法規名 條號」；expect_case 走路案號；
    expect_none 期望引擎直接回 no_match，neg_rate 報告）。"""
    base = Path(__file__).resolve().parents  # [1]=/app(container 內 evals 掛載), [2]=repo 根(本機)
    path = base[1] / "evals" / "questions.json"
    if not path.exists():
        path = base[2] / "evals" / "questions.json"
    items = json.loads(path.read_text(encoding="utf-8"))
    hit = 0
    tested = 0
    neg_tested = 0
    neg_hit = 0
    for it in items:
        if it.get("expect_none"):
            # 負面題：期望引擎直接回「沒有符合比對的法條」（不問 LLM）
            neg_tested += 1
            res = await rag.answer(it["q"], recall=50, top_k=5)
            if res.get("no_match"):
                neg_hit += 1
            continue
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
            "hit_rate": round(hit / tested, 3) if tested else 0.0,
            "neg_tested": neg_tested, "neg_hit": neg_hit,
            "neg_rate": round(neg_hit / neg_tested, 3) if neg_tested else 0.0}
