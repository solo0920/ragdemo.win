"""FastAPI：/health /ready /ingest /query /eval /hosts /settings/*，模型與服務全走環境變數。

`/health` 是 liveness（永遠 200、零探測）；`/ready` 是 readiness（真的探依賴鏈，
不 ready 回 503）。分工理由見 app/readiness.py。

唯一的例外是 `/settings/default-model` 的 `default_model`：那是**本機的使用者設定**
（存本機 pg，見 host_settings.py），會取代該機的 `LLM_MODEL` 成為查詢時的實際預設。

⚠️ `/settings/*` 底下兩個端點（`default-model`、`probe-clouds`）**必須**同時列進
frontend `+server.ts` 的 `SENSITIVE`，否則 `guard()` 對不在清單裡的路徑直接
`return null`、完全不驗 session（2026-10-02 修過一次同型漏洞）。
"""
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

from . import cloud_probe, host_settings, rag, readiness, registry, rules_store as rules
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


@app.get("/ready")
async def ready(force: int = 0):
    """readiness：「這台現在**能不能服務一次查詢**」（真的探依賴鏈）。

    與 `/health`（liveness，永遠 200、零探測）的分工理由見 app/readiness.py 的
    模組 docstring。一句話：`/health` 被同儕面板與 wait-stack.sh 依賴，它們要的
    是「進程活著」；把「某個依賴壞」呈現成「這台死了」是這個專案反覆在修的錯誤。

    503 的條件是「任一**必要**依賴不是 up」—— 含「無從驗證」（探測逾時），
    因為不能驗證就不能承諾。但 `checks[x].verdict` 會分開說明是 `down` 還是
    `unknown`，別把兩者讀成同一件事。

    `?force=1` 跳過 TTL 快取（「我剛修好但還是不 ready」的排查用）。
    """
    body = await readiness.report(force=bool(force))
    if not body["ok"]:
        return JSONResponse(status_code=503, content=body)
    return body


@app.post("/settings/probe-clouds")
async def probe_clouds(force: int = 0):
    """探各雲端 provider 的 catalog：**每個 provider 一次** `/models` 呼叫。

    冪等、可重複呼叫、**不改任何狀態**。回應不含任何憑證值。

    ⚠️ **只打 catalog 端點，不打任何推理端點** —— 推理會燒額度（free 額度是共享池）。

    ⚠️ **這個路徑必須進 frontend 的 `SENSITIVE` 清單**（`+server.ts`）。
       `guard()` 對不在清單裡的路徑直接 `return null`、**完全不驗 session**，
       2026-10-02 剛修過一次同型漏洞（要加的字串：`settings/probe-clouds`）。
       連同 `settings/default-model`，兩個設定寫入／探測端點都要登入。

    `?force=1` 跳過 TTL 快取（5 分鐘）—— 「我剛換了 key，現在就探」。
    """
    body = await cloud_probe.probe(force=bool(force))
    return {"ok": True, **body}


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
async def status(probe: int = 1):
    """主機連線探測（連線詳細彈窗用；不依賴 query，供按鈕常駐顯示）。

    law_version：本機法規版本（ChLaw.json 的 UpdateDate）。官方 zip 檔名固定為
    ChLaw.json.zip 不具版本意義，故不用檔名。備援機由 sync-snapshot.sh 寫
    data/laws/.law_version 帶入，見 rag.law_version()。

    probe=0：只回本機資訊，不去探測其他主機。**呼叫別台的 /status 時必須帶**，
    否則 A→B→C→A 互相探測，請求數指數成長（2026-09-26 實作時踩到）。

    `known`：本機從 HOST_API_URLS 知道的 peer 清單（id → 公網網址），讓前端
    的「後端切換器」不必把機台名寫死。**未設 HOST_API_URLS 就是空 dict**，
    前端據此只顯示「自動」—— 單機部署不該被要求知道別人的主機名。
    """
    out = {
        "ok": True,
        "host": registry.HOST_ID,
        "law_version": rag.law_version(),
        "known": dict(rag.HOST_API),
    }
    if probe:
        out["log"] = await rag._host_probe_log()
        out["versions"] = await rag._host_law_versions()
    return out


@app.post("/ingest")
async def ingest(docs: list[Doc]):
    n = await rag.upsert([d.model_dump() for d in docs])
    return {"ingested": n, "collection": rag.COLLECTION}


@app.post("/query")
async def query(q: Query):
    try:
        # model 的優先權：/query 指定的 → 本機存的 default_model → 未指定
        #（未指定時 rag.generate() 走 gateway._llm_model_for(選中的 ollama)，
        #   見 backend/app/host_settings.py 模組 docstring 為什麼不直接塞 LLM_MODEL）
        return await rag.answer(q.question, q.recall, q.top_k,
                                model=await host_settings.resolve(q.model))
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


# ── per-host 預設聊天模型（可在網頁設定；存本機 pg）─────────────────────
# 契約與理由見 backend/app/host_settings.py 的模組 docstring 與 backend/DESIGN.md。
#
# ⚠️ **沒有 `host` 欄位**：這個端點只設定自己這台。三台各有自己的 pg，
#    跨機寫入等於要一套「遠端寫入授權」，而需求不需要 —— 前端用 HOST_API_URLS
#    直接去問／寫每一台就好。
#
# ⚠️ **不掛 ADMIN_TOKEN**（與 /rules 的寫入不同）：這是「這台自己的預設」，
#    沒有跨機影響。對外路徑由 Pages worker 的登入 guard ＋ Cloudflare Access
#    收著（ARCHITECTURE.md〈認證〉）。加上 token 會讓前端多一套憑證分發，
#    換來的只是「能改自己那台預設模型」這件事被擋住。
#
#    ⚠️ **2026-10-02 修正**：上面那句「由 worker 的登入 guard 收著」原本是
#    **不成立的** —— `settings/default-model` 當時不在 worker 的 `SENSITIVE`
#    清單裡，而 `guard()` 對未列出的路徑直接 `return null`，完全不驗 session。
#    當時只有 GET 所以看起來無害，但同一輪加入 `export const PUT` 之後同一條
#    路徑變成**匿名可寫**：任何人都能改掉某台主機的預設聊天模型，症狀是
#    「查詢突然換模型」而不會有任何錯誤。
#    現在 `settings/default-model` **已在** `SENSITIVE` 裡，宣告在
#    `frontend/src/routes/api/[...path]/+server.ts`；守衛是
#    `tests/test_worker_sensitive_paths.py`（它從「哪些路徑會改變狀態」這個
#    **獨立**角度算清單再比對，不是讀同一個常數）。
#    ⚠️ 動 worker 那個清單時，記得同時更新本段與 DESIGN.md ——
#    `test_the_backend_comment_does_not_overclaim_protection` 會紅。
class DefaultModelIn(BaseModel):
    model: str | None = None  # null／空字串＝清除，回到 LLM_MODEL


@app.get("/settings/default-model")
async def default_model_get():
    return host_settings.envelope(await host_settings.stored())


@app.put("/settings/default-model")
async def default_model_put(body: DefaultModelIn):
    try:
        return host_settings.envelope(await host_settings.set_default(body.model))
    except ValueError as e:  # 自己的驗證（例如名稱過長）→ 400
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # ⚠️ 不把 str(e) 回給用戶：asyncpg 的連線錯誤字串會帶 DSN 的形狀。
        # 詳細原因只進 log（寫入端點必須誠實回報失敗，不能假裝成功）。
        logger.error("寫入預設模型失敗：%s", e)
        raise HTTPException(status_code=503, detail="寫入預設模型失敗（資料庫不可用）")


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


# ── law-update：強制更新本機法規版本 ────────────────────────────────────
# 容器跑不了 ingest 管線，所以這裡只寫請求檔；實際動作由 host 端
# scripts/law-update-worker.sh 執行（依角色：來源機跑 sync_daily.py --apply，
# 備援機強制重抓快照）。GET 不設權限（前端要顯示狀態），POST 需 ADMIN_TOKEN。
@app.get("/law-update")
async def law_update_get():
    return {"ok": True, "host": registry.HOST_ID, **rag.law_update_state()}


@app.post("/law-update")
async def law_update_post(authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    r = rag.request_law_update(actor="admin-token")
    if not r.get("ok"):
        raise HTTPException(status_code=409, detail=r.get("reason", "無法接受請求"))
    return r


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
