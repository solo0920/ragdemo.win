"""RAG 管線：gateway.embed -> Qdrant 召回 -> retrieve.rerank(預留) -> LLM 生成。

服務位址一律走候選清單（gateway.OLLAMA_URLS / gateway.QDRANT_URLS），先後順序即優先權：
- 首選「優先權最高且目前可用（TCP＋模型齊備）」者，快取一段時間（gateway.PICK_TTL）。
- 連線錯誤 / model 404 自動降級到下一台；gateway.PICK_TTL 過期會重新掃描，率先主機回復即自動切回。
- 每台 ollama 主機可配不同 LLM model（gateway.OLLAMA_MODELS 與 gateway.OLLAMA_URLS 同順序對應）。
- 模型 keepalive：要求常駐（gateway.KEEP_ALIVE，預設 -1 永久），api 啟動時 gateway.warmup 預載，避免首個 query 冷載入。
"""
import asyncio
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

from . import cn_parse, gateway, law_meta, retrieve
from .common import sparse as _sparse
from .common.text import collapse_ws
from . import law_struct as _law
from . import rules_store as _rules
from . import usage as _usage

logger = logging.getLogger("ragdemo")


class GatewayUnconfigured(Exception):
    """請求 openrouter 閉源模型但 CF gateway 未設定（URL／token 缺一）。"""





# 雲端閉源模型路由（OpenRouter via Cloudflare AI Gateway）：/query 指定 model="openrouter:<id>" 時走此。
# OPENROUTER_GATEWAY_URL＝openai-compatible base（含 /openrouter 尾段）；token 用 gateway.CF_AIG_TOKEN env，
# 未設則自動讀 gateway.CF_AIG_TOKEN_FILE（預設 ~/.config/opencode/cf-aig-token，本機 demo 即測即用）。
OPENROUTER_GATEWAY_URL = os.getenv("OPENROUTER_GATEWAY_URL", "").rstrip("/")
OPENROUTER_MODELS = [m.strip() for m in os.getenv(
    "OPENROUTER_MODELS",
    # CF AI Gateway→OpenRouter 目前提供 18 個 :free 模型（2026-09-26 實測），全部納入下拉選單；
    # free 額度共享池（50/天），選項只是列出、額度用罄時上游回 429。
    "cohere/north-mini-code:free,dots-studio/dots-3-note-preview:free,"
    "google/gemma-4-26b-a4b-it:free,google/gemma-4-31b-it:free,"
    "inclusionai/ling-3.0-flash-fin:free,inclusionai/ling-3.0-flash-sante:free,"
    "liquid/lfm-2.5-2.6b:free,nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free,"
    "nvidia/nemotron-3-super-120b-a12b:free,nvidia/nemotron-3-ultra-550b-a55b:free,"
    "nvidia/nemotron-3.5-content-safety:free,nvidia/nemotron-3.5-lightning:free,"
    "poolside/laguna-s-2.1:free,poolside/laguna-xs-2.1:free,"
    "qwen/qwen3.8-27b:free,thinkingmachines/inkling-small:free,"
    "thinkingmachines/inkling:free,z-ai/glm-5.2:free",
).split(",") if m.strip()]
# OpenCode Zen（free 模型）：model="zen/<id>" 時走 zen 的 openai-compatible chat/completions。
# ZEN_API_KEY 留空時路由已備好、但呼叫會回清楚錯誤（選到時前端提示「Zen key 未設定」）。
ZEN_BASE_URL = os.getenv("ZEN_BASE_URL") or "https://opencode.ai/zen/v1".rstrip("/")
ZEN_API_KEY = os.getenv("ZEN_API_KEY", "").strip()
ZEN_FREE_MODELS = [m.strip() for m in os.getenv(
    "ZEN_FREE_MODELS",
    "deepseek-v4-flash-free,muse-spark-1.3-contributor-free,mimo-v2.6-flash-free,"
    "mimo-v2.5-free,ling-3.0-flash-fin-free,nemotron-3-ultra-free,"
    "nemotron-3.5-lightning-free,space-bunny-free",
).split(",") if m.strip()]
# NVIDIA NIM（build.nvidia.com）：model="nv/<id>" 走 integrate.api.nvidia.com（OpenAI-compatible）。
# 用 NVIDIA_API_KEY（nvapi-...）；未設時拋 GatewayUnconfigured（下拉選單標「需 NIM key」）。
# 路由前綴用「nv/」而非「nvidia/」，避免與模型自身 org 前綴（nvidia/nemotron-…）碰撞。
NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL") or "https://integrate.api.nvidia.com/v1".rstrip("/")
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "").strip()
NVIDIA_MODELS = [m.strip() for m in os.getenv(
    "NVIDIA_MODELS",
    # build.nvidia.com 實測可呼叫的模型（2026-09-26，key nvapi-… 通過）：
    "nvidia/nemotron-3.5-lightning-30b-a3b,nvidia/nemotron-3-super-120b-a12b,"
    "z-ai/glm-5.3-flash,deepseek-ai/deepseek-v4.1-flash,mistralai/mistral-nemotron",
).split(",") if m.strip()]
# Google Gemini（經 Cloudflare AI Gateway 的 google-ai-studio provider）：model="gemini/<id>"。
# Gemini key 在 gateway 後台新增（AI Studio key）；走原生 generateContent 一 stage（非 streaming）。
# 注意：gemini-2.5-* 對新 key 回 404「no longer available to new users」、高峰時期可能是 503 UNAVAILABLE。
GEMINI_GATEWAY_ID = os.getenv("CF_AIG_GATEWAY_ID") or "cloudflaregateway"
GEMINI_GATEWAY_URL = os.getenv("GEMINI_GATEWAY_URL", "").rstrip("/") or (
    OPENROUTER_GATEWAY_URL.removesuffix("/openrouter") + "/google-ai-studio" if OPENROUTER_GATEWAY_URL else "-"
)
GEMINI_MODELS = [m.strip() for m in os.getenv(
    "GEMINI_MODELS",
    # AI Studio 新 key 可用、實測 200 的模型（2026-09-26）：
    "gemini-3.8-flash,gemini-3.5-flash,gemini-flash-latest,gemini-flash-lite-latest",
).split(",") if m.strip()]
GROQ_GATEWAY_URL = os.getenv("GROQ_GATEWAY_URL", "").rstrip("/") or (
    OPENROUTER_GATEWAY_URL.removesuffix("/openrouter") + "/groq/v1" if OPENROUTER_GATEWAY_URL else "-"
)
GROQ_MODELS = [m.strip() for m in os.getenv(
    "GROQ_MODELS",
    # CF gateway→groq 實測可呼叫（openai-compatible chat/completions）：
    "openai/gpt-oss-120b,openai/gpt-oss-20b,qwen/qwen3.8-27b",
).split(",") if m.strip()]
# Cohere（經 Cloudflare AI Gateway 的 cohere provider）：model="cohere/<id>"。
# Cohere key 在 gateway 後台新增；走原生 /v1beta/chat（非 openai-compatible），回應取 text 欄位。
COHERE_GATEWAY_URL = os.getenv("COHERE_GATEWAY_URL", "").rstrip("/") or (
    OPENROUTER_GATEWAY_URL.removesuffix("/openrouter") + "/cohere/v1beta" if OPENROUTER_GATEWAY_URL else "-"
)
COHERE_MODELS = [m.strip() for m in os.getenv(
    "COHERE_MODELS",
    # CF gateway→cohere 實測可呼叫、法律中文問答佳（ep=chat）：
    "command-a-plus-05-2026,command-a-03-2025,command-r-plus-08-2024",
).split(",") if m.strip()]
# Hugging Face Inference Providers（router.huggingface.co，OpenAI-compatible）：model="hf/<id>"。
# 註：CF AI Gateway 的 huggingface provider 目前指向已下架 api-inference.huggingface.co（530 Origin DNS error），
# 故直接接官方新端點 router.huggingface.co/v1（FAI 2026-09-26 實測 OK）。用 HF token（hf_...）認證。
HF_BASE_URL = os.getenv("HF_BASE_URL") or "https://router.huggingface.co/v1".rstrip("/")
HF_TOKEN = os.getenv("HF_TOKEN", "").strip()
HF_MODELS = [m.strip() for m in os.getenv(
    "HF_MODELS",
    # router.huggingface.co 實測可呼叫、中文法律問答佳（2026-09-26）：
    "deepseek-ai/DeepSeek-V4.1-Flash,Qwen/Qwen3.8-27B,zai-org/GLM-5.3-Flash,meta-llama/Llama-3.3-70B-Instruct",
).split(",") if m.strip()]
# Mistral（經 Cloudflare AI Gateway 的 mistral provider）：model="mis/<id>"（openai-compatible）。
# Mistral key 在 gateway 後台新增、gateway 代管；此處用 CF token。
# 註：mistral-small/medium 家族上游 rate limit（429 code 1300），實測可用 ministral-8b / codestral。
MISTRAL_GATEWAY_URL = os.getenv("MISTRAL_GATEWAY_URL", "").rstrip("/") or (
    OPENROUTER_GATEWAY_URL.removesuffix("/openrouter") + "/mistral/v1" if OPENROUTER_GATEWAY_URL else "-"
)
MISTRAL_MODELS = [m.strip() for m in os.getenv(
    "MISTRAL_MODELS",
    # CF gateway→mistral 實測 200：ministral-8b-latest、codestral-latest
    "ministral-8b-latest,codestral-latest",
).split(",") if m.strip()]
# JEV（TypeSafe System One 決策模型）：只做 Noul 驗證，不當計數/日期/主管機關的題庫。
# 全走 fail-open：任何失敗（網路／超時／無 key）回 None，退回原本規則邏輯，絕不擋 query。
TYPESAFE_KEY = os.getenv("TYPESAFE_API_KEY", "").strip()
TYPESAFE_URL = os.getenv("TYPESAFE_URL") or "https://api.typesafe.ai/v1/systemone".rstrip("/")
JEV_MODEL = os.getenv("JEV_MODEL") or "jev-latest"
JEV_DISABLED = os.getenv("JEV_DISABLED", "") in ("1", "true", "True", "yes")
try:
    JEV_VERIFY_MIN = float(os.getenv("JEV_VERIFY_MIN") or "0.4")  # 校準樣本：0.26 該退、0.5/0.89 該留
except ValueError:
    JEV_VERIFY_MIN = 0.4
try:
    JEV_BANK_MIN = float(os.getenv("JEV_BANK_MIN") or "0.6")  # 題庫採用閘門：比驗證更嚴（採用即固定答案）
except ValueError:
    JEV_BANK_MIN = 0.6
_jev_fails = 0        # 連續失敗次數（熔斷用）
_jev_until = 0.0      # 熔斷截止（unix 秒）；期間直接跳過，避免每 query 卡 timeout







SYSTEM = "你是法規判決檢索助理。只依據提供的資料回答，並標註案號/條號；若資料與問題無關或僅模糊相關，直接回「沒有符合比對的法條」，不要編造、不要臆測。回答某條時，除主旨外若該條含款/項，請說明其下共幾項、幾款並摘要各款要旨。"









































# ── 法規版本（前端「法規版本」欄位）────────────────────────────────────────
# 官方 zip 檔名是固定的 ChLaw.json.zip（實測 Content-Disposition），永遠不變，
# 拿它當版本等於三台顯示同一串字。因此版本一律取 ChLaw.json 內的 UpdateDate。
# 兩個來源（備援機不跑 ingest，只吃快照，所以必須靠同步腳本把版本帶過來）：
#   1. data/laws/.law_sync.json  → 執行 sync_daily.py 的主機（x570）寫入
#   2. data/laws/.law_version    → sync-snapshot.sh 同步成功後寫入的 sidecar
# 容器內 data/laws 是唯讀掛載，但「讀」不受限；寫入一律在 host 端由 cron 進行。
_LAW_VERSION_CACHE: tuple[float, dict] = (0.0, {})
_LAW_VERSION_TTL = 30.0  # 秒；避免 /status 每次都碰磁碟（前端會定期輪詢）
# 候選基底路徑：原生執行時是 repo 的 data/laws，容器內掛在 /app/data/laws。
# 提成常數是為了讓測試能乾淨地改掉它，而不必改寫整個函式。
#
# ⚠️ 這裡的相對路徑 `data/laws` 在容器裡解析成 `/app/data/laws`，
# **剛好就是掛載點**（compose 掛 ./data/laws:/app/data/laws），所以
# 「第一個 is_dir() 成立的」這個寫法在這裡是無害的。
# 下方 `_ops_dir()` 的相對路徑則**不**剛好命中 —— 那個才是真的陷阱，
# 別把兩種寫法當成等價的。差異的原因：ops 掛在 `/app/ops`（不在 /app/data 底下）。
_LAW_VERSION_DIRS = (Path("data/laws"), Path("/app/data/laws"))

# ── law-update：請求/狀態檔案通道 ────────────────────────────────────────
# 容器無法執行 ingest 管線（image 沒有 ingest/、data/laws 唯讀、沒裝 duckdb），
# 管線是 host 端的 uv 工具鏈。所以容器只「記錄請求」＋「回報狀態」，
# 實際動作交給 host 端 scripts/law-update-worker.sh 執行。
# 三個檔案都在 data/.ops/（host 與容器共用）：request（api 寫）、
# running（worker 寫）、status（worker 寫）。
# ── law-update：請求/狀態檔案通道 ────────────────────────────────────────
# 容器無法執行 ingest 管線（image 沒有 ingest/、data/laws 唯讀、沒裝 duckdb），
# 管線是 host 端的 uv 工具鏈。所以容器只「記錄請求」＋「回報狀態」，
# 實際動作交給 host 端 scripts/law-update-worker.sh 執行。
# 三個檔案都在 data/.ops/（host 與容器共用）：request（api 寫）、
# running（worker 寫）、status（worker 寫）。
#
# ⚠️⚠️ 容器裡 `data/.ops` 掛在 **`/app/ops`**，不是 `/app/data/.ops`。
# 而 cwd 是 `/app`，所以相對路徑 `data/.ops` 會解析成 `/app/data/.ops`
# —— **那不是掛載點**。compose 只掛了 `/app/data/laws` 與 `/app/data/rules`
# （那兩個是為了能落在 `/app/data` 底下），`data/.ops` 掛在 `/app/ops`。
#
# 舊版用「第一個 `p.parent.is_dir()` 成立的」，所以**運氣好才沒出事**：
# `/app/data/.ops` 今天不存在 → 正確 fallback 到 `/app/ops`。但只要它哪天
# 出現（Dockerfile 多 COPY 一層、有人在 image 裡放了個 data/.ops、
# 或有人加一個 ./data 全目錄的掛載），就會**靜默**選到錯的那個：
#   _ops_write 回 True（寫成功）　can_update 回 True（按鈕亮著）
#   而 host 端 worker 讀的是 /app/ops → **永遠收不到請求**
#   → 前端「更新」按鈕按下沒反應，且沒有任何錯誤。
# 那是「看起來正常但功能是死的」的故障，所以判準不能是「目錄在不在」。
#
# 2026-10-02 mbp 查到這個潛在風險（實測讓 /app/data/.ops 出現過一次，
# 確認會選錯），改成明確判斷自己在不在容器裡。
# 判據用 `/app/app`：Dockerfile 是 WORKDIR /app ＋ COPY app ./app，
# 所以容器裡必然有 /app/app；host 端執行時程式在 backend/app/，不會有。
# 對照 `_LAW_VERSION_DIRS`（:205）—— 那個相對路徑 `data/laws` 在容器裡
# 解析成 `/app/data/laws` **剛好就是掛載點**，所以那個寫法是無害的。
# 兩個清單看起來一樣、語意卻不同，這就是為什麼這裡要註解清楚。
_OPS_DIR_HOST = Path("data/.ops")
_OPS_DIR_CONTAINER = Path("/app/ops")
OPS_NAME = "law-update"


def _in_container() -> bool:
    """「我在容器裡嗎」—— 判據是**程式碼自己的位置**，不是 cwd。

    Dockerfile 是 `WORKDIR /app` ＋ `COPY app ./app`（app.main 由 uvicorn 載入），
    所以容器裡必然有 `/app/app`；host 端執行時程式在 `backend/app/`，
    `/app/app` 不存在（實測 wsl／mbp／x570 三台都不存在）。

    刻意**不**用 cwd 或 `/app/ops` 判斷：前者會讓「從哪個目錄啟動」變成
    隱藏輸入，後者就是待判斷的對象本身（拿它判斷等於假設結論）。
    """
    return Path("/app/app").is_dir()


def _ops_dir() -> Path | None:
    """容器裡只認 `/app/ops`；掛載掉了就回 None（讓 can_update 變 False）。

    回 None 而不是 fallback 到別處，是刻意的：`law_update_state()` 的
    `can_update` 會因此變成 False，前端把按鈕 disable 掉 —— 那是「看得見的
    壞掉」。fallback 的話按鈕亮著但按了沒反應，那看不見。
    """
    d = _OPS_DIR_CONTAINER if _in_container() else _OPS_DIR_HOST
    return d if d.is_dir() else None


def _ops_path(name: str) -> Path | None:
    d = _ops_dir()
    return d / name if d else None


def _ops_read(name: str) -> dict:
    p = _ops_path(name)
    if not p or not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _ops_write(name: str, data: dict) -> bool:
    """原子寫入（先 .tmp 再 rename），避免 worker 讀到寫到一半的 JSON。"""
    p = _ops_path(name)
    if not p:
        return False
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)
        return True
    except Exception as e:
        logger.warning("law-update 寫入 %s 失敗: %s", p, e)
        try:
            tmp.unlink()
        except Exception:
            pass
        return False


def law_update_state() -> dict:
    """GET /law-update：目前有沒有請求在飛、上一個結果如何。"""
    return {
        "pending": bool(_ops_read(f"{OPS_NAME}.request")),
        "running": bool(_ops_read(f"{OPS_NAME}.running")),
        "last": _ops_read(f"{OPS_NAME}.status"),
        "can_update": _ops_path("probe") is not None,
    }


def request_law_update(actor: str) -> dict:
    """POST /law-update：寫下請求檔。已有請求或正在執行就拒絕（避免疊請求）。"""
    if _ops_path("probe") is None:
        return {"ok": False, "reason": "ops 目錄不可用（容器未掛載 data/.ops）"}
    if _ops_read(f"{OPS_NAME}.running"):
        return {"ok": False, "reason": "已有更新在執行中，請等它完成"}
    if _ops_read(f"{OPS_NAME}.request"):
        return {"ok": False, "reason": "已有更新請求排隊中"}
    ok = _ops_write(f"{OPS_NAME}.request", {
        "requested_at": datetime.now().isoformat(timespec="seconds"),
        "requested_by": actor,
        "host_id": gateway.HOST_ID,
        "current_version": law_version().get("update_date", ""),
    })
    if not ok:
        return {"ok": False, "reason": "寫入請求檔失敗"}
    return {"ok": True, "message": "已排入更新，實際動作由主機端 worker 執行（需數分鐘）"}


def _norm_law_date(raw: str) -> str:
    """把官方的 UpdateDate 正規化成 ISO 日期（YYYY-MM-DD）。

    官方 ChLaw.json 的 UpdateDate 是中文格式，例如「2026/9/18 上午 12:00:00」
    （2026-09-26 實測），不是 ISO。前端要比較新舊、判斷「本機是否落後」，
    必須靠正規化後的字串比較；直接把原字串給前端會讓比對靜默失效。
    回空字串代表無法解析（前端顯示 '—'，不猜）。
    """
    s = (raw or "").strip()
    if not s:
        return ""
    m = re.match(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", s)
    if not m:
        # 已經是 ISO 就原樣回（避免二次處理破壞）
        return s if re.match(r"^\d{4}-\d{2}-\d{2}$", s) else ""
    y, mo, d = (int(g) for g in m.groups())
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return ""
    return f"{y:04d}-{mo:02d}-{d:02d}"


def _read_law_version() -> dict:
    """回 {update_date, raw, source, at}；讀不到回 {}（前端顯示 '-'）。"""
    for base in _LAW_VERSION_DIRS:
        # 備援機的 sidecar 優先於主機的 .law_sync.json：兩者若同時存在，
        # sidecar 代表「實際服務的資料版本」，.law_sync.json 只是本機曾下載過的版本。
        for name in (".law_version", ".law_sync.json"):
            p = base / name
            try:
                if not p.exists():
                    continue
                d = json.loads(p.read_text(encoding="utf-8"))
                raw = (d.get("update_date") or "").strip()
                if raw:
                    return {"update_date": _norm_law_date(raw), "raw": raw,
                            "source": d.get("source") or name.lstrip("."),
                            "at": d.get("synced_at") or d.get("last_checked") or ""}
            except Exception as e:  # 壞檔不讓 /status 整個 500
                logger.debug("law_version 讀取失敗 %s: %s", p, e)
    return {}


def law_version() -> dict:
    """本機法規版本（帶短 TTL 快取）。"""
    global _LAW_VERSION_CACHE
    ts, val = _LAW_VERSION_CACHE
    now = time.time()
    if now - ts < _LAW_VERSION_TTL:
        return val
    val = _read_law_version()
    _LAW_VERSION_CACHE = (now, val)
    return val

































































async def _nvidia_complete(model: str, prompt: str) -> str:
    """NVIDIA NIM（integrate.api.nvidia.com）chat/completions；需 NVIDIA_API_KEY（nvapi-...）。"""
    if not NVIDIA_API_KEY:
        raise GatewayUnconfigured("NVIDIA_API_KEY 未設定：無法走 NVIDIA NIM")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
    }
    url = f"{NVIDIA_BASE_URL}/chat/completions"
    async with httpx.AsyncClient(timeout=300) as c:
        r = await c.post(url, headers=headers,
                         json={"model": model, "messages": [{"role": "user", "content": prompt}],
                               "stream": False, "max_tokens": 500})
    _rstatus(r, f"nvidia/{model}")
    j = r.json()
    await _usage.track("nvidia", model, tokens=_resp_tokens(j, "openai"))
    return j["choices"][0]["message"]["content"]


async def _gemini_complete(model: str, prompt: str) -> str:
    """Google Gemini（經 CF AI Gateway google-ai-studio provider）generateContent；
    Gemini key 在 gateway 後台代管，此處用 CF token（cf-aig-authorization）認證即可。"""
    if not GEMINI_GATEWAY_URL or GEMINI_GATEWAY_URL == "-":
        raise GatewayUnconfigured("GEMINI_GATEWAY_URL 未設定：走不了 Google Gemini（需 CF AI Gateway 的 google-ai-studio 路由）")
    tok = gateway._gateway_token()
    if not tok:
        raise GatewayUnconfigured("CF_AIG_TOKEN 未設定：無法走 Google Gemini")
    headers = {
        "Content-Type": "application/json",
        "cf-aig-authorization": f"Bearer {tok}",
        "Authorization": f"Bearer {tok}",
    }
    url = f"{GEMINI_GATEWAY_URL}/v1beta/models/{model}:generateContent"
    async with httpx.AsyncClient(timeout=300) as c:
        r = await c.post(url, headers=headers,
                         json={"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                               "generationConfig": {"maxOutputTokens": 500}})
    _rstatus(r, f"gemini/{model}")
    j = r.json()
    await _usage.track("gemini", model, tokens=_resp_tokens(j, "gemini"))
    parts = j["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts)


async def _groq_complete(model: str, prompt: str) -> str:
    """Groq（經 CF AI Gateway groq provider）chat/completions（openai-compatible）；
    Groq key 在 gateway 後台代管，此處用 CF token（cf-aig-authorization）認證即可。"""
    if not GROQ_GATEWAY_URL or GROQ_GATEWAY_URL == "-":
        raise GatewayUnconfigured("GROQ_GATEWAY_URL 未設定：走不了 Groq（需 CF AI Gateway 的 groq 路由）")
    tok = gateway._gateway_token()
    if not tok:
        raise GatewayUnconfigured("CF_AIG_TOKEN 未設定：無法走 Groq")
    headers = {
        "Content-Type": "application/json",
        "cf-aig-authorization": f"Bearer {tok}",
        "Authorization": f"Bearer {tok}",
    }
    url = f"{GROQ_GATEWAY_URL}/chat/completions"
    async with httpx.AsyncClient(timeout=300) as c:
        r = await c.post(url, headers=headers,
                         json={"model": model, "messages": [{"role": "user", "content": prompt}],
                               "stream": False, "max_tokens": 500})
    _rstatus(r, f"groq/{model}")
    j = r.json()
    await _usage.track("groq", model, tokens=_resp_tokens(j, "openai"))
    return j["choices"][0]["message"]["content"]


async def _cohere_complete(model: str, prompt: str) -> str:
    """Cohere（經 CF AI Gateway cohere provider）原生 /v1beta/chat；
    Cohere key 在 gateway 後台代管，此處用 CF token（cf-aig-authorization）認證即可。"""
    if not COHERE_GATEWAY_URL or COHERE_GATEWAY_URL == "-":
        raise GatewayUnconfigured("COHERE_GATEWAY_URL 未設定：走不了 Cohere（需 CF AI Gateway 的 cohere 路由）")
    tok = gateway._gateway_token()
    if not tok:
        raise GatewayUnconfigured("CF_AIG_TOKEN 未設定：無法走 Cohere")
    headers = {
        "Content-Type": "application/json",
        "cf-aig-authorization": f"Bearer {tok}",
        "Authorization": f"Bearer {tok}",
    }
    url = f"{COHERE_GATEWAY_URL}/chat"
    async with httpx.AsyncClient(timeout=300) as c:
        r = await c.post(url, headers=headers,
                         json={"model": model, "message": prompt, "max_tokens": 500})
    _rstatus(r, f"cohere/{model}")
    j = r.json()
    await _usage.track("cohere", model, tokens=_resp_tokens(j, "cohere"))
    return j.get("text", "")


async def _hf_complete(model: str, prompt: str) -> str:
    """Hugging Face Inference Providers（router.huggingface.co）chat/completions（OpenAI-compatible）；
    需 HF_TOKEN（hf_...，Settings→Access Tokens 選 Fine-grained + Inference preset）。"""
    if not HF_TOKEN:
        raise GatewayUnconfigured("HF_TOKEN 未設定：無法走 Hugging Face Inference Providers")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {HF_TOKEN}",
    }
    url = f"{HF_BASE_URL}/chat/completions"
    async with httpx.AsyncClient(timeout=300) as c:
        r = await c.post(url, headers=headers,
                         json={"model": model, "messages": [{"role": "user", "content": prompt}],
                               "stream": False, "max_tokens": 500})
    _rstatus(r, f"hf/{model}")
    j = r.json()
    await _usage.track("hf", model, tokens=_resp_tokens(j, "openai"))
    msg = j["choices"][0]["message"]
    # HF 上部分推理模型（如 DeepSeek-V4.1-Flash）把內容放 reasoning_content、content 可能 None
    return msg.get("content") or msg.get("reasoning_content") or ""


async def _mistral_complete(model: str, prompt: str) -> str:
    """Mistral（經 CF AI Gateway mistral provider）chat/completions（openai-compatible）；
    Mistral key 在 gateway 後台代管，此處用 CF token（cf-aig-authorization）認證即可。"""
    if not MISTRAL_GATEWAY_URL or MISTRAL_GATEWAY_URL == "-":
        raise GatewayUnconfigured("MISTRAL_GATEWAY_URL 未設定：走不了 Mistral（需 CF AI Gateway 的 mistral 路由）")
    tok = gateway._gateway_token()
    if not tok:
        raise GatewayUnconfigured("CF_AIG_TOKEN 未設定：無法走 Mistral")
    headers = {
        "Content-Type": "application/json",
        "cf-aig-authorization": f"Bearer {tok}",
        "Authorization": f"Bearer {tok}",
    }
    url = f"{MISTRAL_GATEWAY_URL}/chat/completions"
    async with httpx.AsyncClient(timeout=300) as c:
        r = await c.post(url, headers=headers,
                         json={"model": model, "messages": [{"role": "user", "content": prompt}],
                               "stream": False, "max_tokens": 500})
    _rstatus(r, f"mistral/{model}")
    j = r.json()
    await _usage.track("mistral", model, tokens=_resp_tokens(j, "openai"))
    return j["choices"][0]["message"]["content"]


def _resp_tokens(j: dict, kind: str) -> int:
    """從各 provider LLM 回應中提取 token 用量（各家格式不同；取不到回 0）。"""
    try:
        if kind == "gemini":
            return int(j.get("usageMetadata", {}).get("totalTokenCount") or 0)
        if kind == "cohere":
            u = j.get("usage", {}) or {}
            t = u.get("tokens", {}) or {}
            return int(t.get("input_tokens") or 0) + int(t.get("output_tokens") or 0)
        if kind == "ollama":
            return int(j.get("prompt_eval_count") or 0) + int(j.get("eval_count") or 0)
        u = j.get("usage", {}) or {}
        tt = u.get("total_tokens") or u.get("totalTokens") or 0
        if tt:
            return int(tt)
        return int(u.get("prompt_tokens") or 0) + int(u.get("completion_tokens") or 0)
    except (KeyError, TypeError, ValueError):
        return 0


# 限流標記：{ provider/model key: {"until": epoch_sec, "note": str} }
# 由 _rstatus() 在收到 429 時記錄；成功呼叫時清除（還原）。記憶體即可（伺服器單 worker）。
_LIMITED: dict[str, dict] = {}

# 各 provider free 額度（來源 github.com/mnfst/awesome-free-llm-apis，2026-09-26）。
# 注意：該清單對我們在用的 provider 給的全是「請求次數」型（RPD／月），
# 無 token 型額度（"token 當分母"只存在於該站 Aion 20K TPD / SiliconFlow 50K TPM 等
# 未接的 provider）。故分母一律為請求次數、分子為今日 calls；無公布的（HF/Mistral/Zen
# credit 計費）標 None → 不顯示分數、維持純「今日 N次/Tk」。
FREE_QUOTA: dict[str, dict | None] = {
    "openrouter": {"limit": 50, "period": "day"},       # free 模型 50 RPD／20 RPM（per model）
    "nvidia":     {"limit": 10000, "period": "day"},    # 40 RPM, 10,000 RPD
    "gemini":     {"limit": 1500, "period": "day"},     # 15-30 RPM, 1,500 RPD
    "groq":       {"limit": 1000, "period": "day"},     # 30 RPM, 1,000 RPD（compound 250）
    "cohere":     {"limit": 1000, "period": "month"},   # 1,000 calls/month
    "hf":         None,                                 # $0.10/month credit（無次數額度）
    "mistral":    None,                                 # $10/month credit（無次數額度）
    "zen":        None,                                 # 未公布額度
    "ollama":     None,                                 # 本機、無額度
}


def _limited_snapshot() -> list[dict]:
    """當前未解除的限流 provider/model（供 /models 附帶，前端標紅）。"""
    now = time.time()
    out = []
    for key, info in _LIMITED.items():
        if info["until"] > now:
            out.append({"key": key, "until": info["until"], "note": info.get("note", "")})
    return out


def _mark_limited(key: str, r) -> None:
    """記錄 429 限流：重置時間依 header 推估——
    Retry-After 秒、X-RateLimit-Reset(epoch)、X-RateLimit-*-req-minute 每分鐘，
    皆無 → 預設 1 小時。"""
    try:
        secs = None
        for h in ("retry-after", "Retry-After", "X-RateLimit-Reset", "x-ratelimit-reset"):
            v = r.headers.get(h)
            if not v:
                continue
            try:
                val = float(v)
                if h.lower() == "x-ratelimit-reset" and val > 1e12:  # epoch ms
                    val /= 1000
                secs = val if h.lower() == "retry-after" else val - time.time()
                break
            except ValueError:
                continue
        if secs is None:
            for h in r.headers.keys():
                if "req-minute" in h.lower() or "req-month" in h.lower():
                    secs = 60  # 分鐘型額度表 → 每分鐘重置
                    break
        until = time.time() + secs if secs and secs > 0 else time.time() + 3600
        _LIMITED[key] = {"until": until, "note": ""}
    except Exception:
        _LIMITED[key] = {"until": time.time() + 3600, "note": ""}


def _rstatus(r, key: str) -> None:
    """取代 r.raise_for_status()：429 時標記限流再 re-raise；成功（2xx）清除限流。"""
    if 200 <= r.status_code < 300:
        _LIMITED.pop(key, None)
        return
    if r.status_code == 429:
        _mark_limited(key, r)
    try:
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise e


async def _zen_complete(model: str, prompt: str) -> str:
    """OpenCode Zen（free 模型）chat/completions；需 ZEN_API_KEY（opencode.ai/zen 主控台產生）。"""
    if not ZEN_API_KEY:
        raise GatewayUnconfigured("ZEN_API_KEY 未設定：無法走 OpenCode Zen（free 僅限 OpenCode 內，外部需 key）")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {ZEN_API_KEY}",
    }
    url = f"{ZEN_BASE_URL}/chat/completions"
    async with httpx.AsyncClient(timeout=300) as c:
        r = await c.post(url, headers=headers,
                         json={"model": model, "messages": [{"role": "user", "content": prompt}],
                               "stream": False, "max_tokens": 500})
    _rstatus(r, f"zen/{model}")
    j = r.json()
    await _usage.track("zen", model, tokens=_resp_tokens(j, "openai"))
    return j["choices"][0]["message"]["content"]


async def _openrouter_complete(model: str, prompt: str) -> str:
    """經 Cloudflare AI Gateway 呼叫 OpenRouter 閉源模型（chat/completions）。"""
    if not OPENROUTER_GATEWAY_URL:
        raise GatewayUnconfigured("OPENROUTER_GATEWAY_URL 未設定：無法走 OpenRouter 閉源模型")
    tok = gateway._gateway_token()
    if not tok:
        raise GatewayUnconfigured("CF_AIG_TOKEN 未設定：無法走 OpenRouter 閉源模型")
    headers = {
        "Content-Type": "application/json",
        "cf-aig-authorization": f"Bearer {tok}",
        "Authorization": f"Bearer {tok}",
    }
    url = f"{OPENROUTER_GATEWAY_URL}/chat/completions"
    async with httpx.AsyncClient(timeout=300) as c:
        r = await c.post(url, headers=headers,
                         json={"model": model, "messages": [{"role": "user", "content": prompt}],
                               "stream": False, "max_tokens": 500})
    _ = r
    _rstatus(r, f"openrouter/{model}")
    _ = r
    j = r.json()
    await _usage.track("openrouter", model, tokens=_resp_tokens(j, "openai"))
    return j["choices"][0]["message"]["content"]


# 走雲端 provider 的模型前綴（`generate()` 依這些前綴分流）。抽成常數是為了讓
# `readiness` 能判斷「這個 default_model 是不是 ollama 模型」—— 不是的話就不該
# 去 ollama 的 `/api/tags` 裡找它（那裡永遠不會有 `openrouter/…`）。
#
# ⚠️ 與 `generate()` 的分流必須一致：`tests/test_readiness.py` 有一條測試直接從
# `generate()` 的原始碼抽出所有 `model.startswith("…")` 字面值比對這個清單，
# 所以「加了 provider 但忘了加進這裡」會是 pytest 紅，不是靜默漏報。
CLOUD_MODEL_PREFIXES = ("openrouter/", "zen/", "nv/", "gemini/", "groq/", "cohere/", "hf/", "mis/")


def is_cloud_model(model: str) -> bool:
    """model 是否走雲端 provider（→ 不是 ollama 模型，不該拿去 ollama 找）。"""
    return bool(model) and model.startswith(CLOUD_MODEL_PREFIXES)


async def generate(question: str, contexts: list[dict], cautious: bool = False,
                   brief_law: str | None = None, model: str = "") -> str:
    blocks = "\n\n".join(f"{retrieve._ref(h)} {h['payload'].get('text', '')}" for h in contexts)
    if brief_law:
        guard = (f"使用者查詢的是《{brief_law}》這部法本身。請只用一到三句話做基本敘述"
                 "（規範領域、大致內容）。嚴禁出現任何條號（第1條、第2條……），"
                 "不得寫『參見／詳見／依／見 第X條』，也不要引用或轉述特定條文的內容。\n\n")
    elif cautious:
        guard = ("材料與問題為中度相關：請依材料回答，並明確標註引用來源（法名＋條號）。"
                 "不要直接說「沒有符合比對的法條」；只有材料確實無法回答該問題（無關或未收錄）"
                 "才回「沒有符合比對的法條」，並於回答時列出已檢索到的相關來源。\n\n")
    else:
        guard = ""
    prompt = f"{SYSTEM}\n\n資料：\n{blocks}\n\n{guard}問題：{question}\n回答（附案號/條號）："
    # 指定 openrouter 閉源模型 → 走 CF gateway（demo 閉源速度／額度共享）；失敗即拋（不降級 ollama，
    # 讓前端明確看到該雲端模型的狀態）。
    if model.startswith("openrouter/"):
        return await _openrouter_complete(model.removeprefix("openrouter/"), prompt)
    # OpenCode Zen free 模型 → 走 zen（需 ZEN_API_KEY；無 key 拋 GatewayUnconfigured）。
    if model.startswith("zen/"):
        return await _zen_complete(model.removeprefix("zen/"), prompt)
    # NVIDIA NIM → 走 integrate.api.nvidia.com（需 NVIDIA_API_KEY）。前綴用 nv/，避免與模型 org 前綴 nvidia/ 衝突。
    if model.startswith("nv/"):
        return await _nvidia_complete(model.removeprefix("nv/"), prompt)
    # Google Gemini（經 CF AI Gateway google-ai-studio provider）→ generateContent（gateway 代管 Google key）。
    if model.startswith("gemini/"):
        return await _gemini_complete(model.removeprefix("gemini/"), prompt)
    # Groq（經 CF AI Gateway groq provider）→ chat/completions（gateway 代管 Groq key）。
    if model.startswith("groq/"):
        return await _groq_complete(model.removeprefix("groq/"), prompt)
    # Cohere（經 CF AI Gateway cohere provider）→ 原生 /v1beta/chat（gateway 代管 Cohere key）。
    if model.startswith("cohere/"):
        return await _cohere_complete(model.removeprefix("cohere/"), prompt)
    # Hugging Face Inference Providers → router.huggingface.co（需 HF_TOKEN）。
    if model.startswith("hf/"):
        return await _hf_complete(model.removeprefix("hf/"), prompt)
    # Mistral（經 CF AI Gateway mistral provider）→ chat/completions（gateway 代管 Mistral key）。
    if model.startswith("mis/"):
        return await _mistral_complete(model.removeprefix("mis/"), prompt)
    # ollama 路線：model 依選中的 ollama 主機而定（gateway.OLLAMA_MODELS 同序對應），或明確指定 model。
    # model 容錯 removeprefix("ollama/")（前端地端選項 value 純名，但外部呼叫可能帶前綴）。
    # 連線錯誤 / 404(model not found) 降級下一台；迴圈可走遍所有候選。
    for _ in range(len(gateway.OLLAMA_URLS) + 1):
        base = await gateway._pick("ollama", gateway.OLLAMA_URLS, probe=gateway._ollama_probe)
        model = (model.removeprefix("ollama/") if model else "") or gateway._llm_model_for(base)
        try:
            async with httpx.AsyncClient(timeout=300) as c:
                r = await c.post(f"{base}/api/generate",
                                 json={"model": model, "prompt": prompt, "stream": False,
                                       "think": False, "keep_alive": gateway.keep_alive_value(),
                                       "options": {"num_predict": 500}})
        except (httpx.ConnectError, httpx.ConnectTimeout):
            gateway._drop("ollama")
            continue
        if r.status_code == 404:
            gateway._drop("ollama")  # 該機沒有此 model → 換下一台
            continue
        _rstatus(r, f"ollama/{model}")
        j = r.json()
        await _usage.track("ollama", model, tokens=_resp_tokens(j, "ollama"))
        return j["response"]
    raise httpx.ConnectError("ollama unreachable")






def _jev_enabled() -> bool:
    return bool(TYPESAFE_KEY) and not JEV_DISABLED


def _jev_snippets(top: list[dict], max_chars: int = 160) -> list[dict]:
    """把 top hits 壓成驗證用的摘錄（law/article/截斷 text）。"""
    out = []
    for h in list(top)[:3]:
        p = h.get("payload", {}) or {}
        out.append({
            "law": p.get("law_name", ""),
            "article": (p.get("article_no") or "").replace(" ", ""),
            "text": collapse_ws(p.get("text", ""))[:max_chars],
        })
    return out


async def _jev_noul(state, instructions: str, true_note: str, false_note: str) -> float | None:
    """TypeSafe systemone 單一 Noul 呼叫。失敗回 None（fail-open），不 raise。"""
    global _jev_fails, _jev_until
    if time.time() < _jev_until:
        return None
    body = {"model": JEV_MODEL, "state": state,
            "questions": {"v": {"type": "noul", "instructions": instructions,
                                "true": true_note, "false": false_note}}}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(TYPESAFE_URL, json=body,
                             headers={"authorization": f"Bearer {TYPESAFE_KEY}"})
            r.raise_for_status()
            prob = float(r.json()["answers"]["v"]["noul"])
        _jev_fails = 0
        return prob
    except Exception as e:
        _jev_fails += 1
        if _jev_fails >= 2:  # 連兩次失敗 → 熔斷 60 秒，避免每 query 卡 10 秒
            _jev_until = time.time() + 60
            _jev_fails = 0
            logger.warning("JEV 連續失敗，熔斷 60 秒（%s）", e)
        else:
            logger.warning("JEV 呼叫失敗（%s）", e)
        return None


async def _jev_verify(question: str, text: str, top: list[dict]) -> float | None:
    """Noul：答案的每個事實主張是否都被摘錄的法條支持（防 LLM 編故事）。"""
    if not _jev_enabled():
        return None
    state = {"question": question, "answer": text, "excerpts": _jev_snippets(top)}
    return await _jev_noul(
        state,
        "Is every factual claim in the answer supported by the excerpted legal provisions, "
        "with no invented article numbers, counts, dates, or institutions?",
        "All factual claims are traceable to the excerpts",
        "Any factual claim is unsupported or invented",
    )


async def _jev_rule_pick(question: str, rule: dict) -> float | None:
    """Noul：題庫候選的「採用」裁決——使用者問題與題庫題目（match）語意相符、
    題庫答案能直接回答此問題。只用於近似命中；完全相符（identity）直接採用、不呼叫。
    失敗回 None（fail-open → 不採用、進 RAG）。"""
    if not _jev_enabled():
        return None
    state = {
        "question": question,
        "stored_question": rule.get("match", ""),
        "stored_answer": rule.get("answer", ""),
        "law": rule.get("law") or "",
        "note": rule.get("note") or "",
    }
    return await _jev_noul(
        state,
        "Is the user's question asking about the same issue as the stored question, "
        "such that the stored answer directly answers this question? Adopt only when "
        "the question is on-topic and the stored answer fully addresses it.",
        "The stored answer directly answers this question",
        "The question differs meaningfully or the answer does not address it",
    )


async def answer(question: str, recall: int = 50, top_k: int = 5, model: str = "") -> dict:
    # ── 題庫第一關（pre-RAG）：法名／條號純正則，不耗 gateway.embed＋Qdrant ──
    brief_law = law_meta._detect_law(question)
    an = cn_parse.extract_article_no(question)
    src = {
        "qdrant": {"host": gateway.host_label(gateway._bases.get("qdrant", gateway.QDRANT_URLS[0])),
                   "url": gateway._bases.get("qdrant", gateway.QDRANT_URLS[0])},
        "llm": {"host": gateway.host_label(gateway._bases.get("ollama", gateway.OLLAMA_URLS[0])),
                "url": gateway._bases.get("ollama", gateway.OLLAMA_URLS[0]),
                "model": model or gateway._llm_model_for(gateway._bases.get("ollama", gateway.OLLAMA_URLS[0]))},
    }
    # 使用者題庫（記憶體庫）：完全相符直接答；近似命中由 JEV 裁決；否決／無候選才進 RAG。
    bank_note = None
    try:
        p = _rules.probe(question, brief_law)
    except Exception:
        p = None
    if p:
        rule = p["rule"]
        base = {"ok": True, "host": gateway.HOST_ID, "no_match": False, "src": src,
                "log": await gateway._host_probe_log()}
        ms = collapse_ws(rule.get("match", ""))[:24]
        if p["identity"]:
            base["answer"] = f"{gateway.HOST_ID}: {rule.get('answer', '')}"
            base["confidence"] = "user_rule"
            base["trace"] = f"題庫:{ms}(identity)"
            return base
        jv = await _jev_rule_pick(question, rule)
        if jv is not None and jv >= JEV_BANK_MIN:
            base["answer"] = f"{gateway.HOST_ID}: {rule.get('answer', '')}"
            base["confidence"] = "user_rule"
            base["trace"] = f"題庫:{ms}｜JEV:{jv:.2f}(採題庫)"
            return base
        bank_note = (f"｜題庫:{ms}否決(JEV:{jv:.2f})" if jv is not None
                     else f"｜題庫:{ms}否決(JEV離線)")
    # 內建規則題庫（不進 LLM）：法名問句命中 count/authority/effective/revised/level/active/brief
    # 任一 intent，直接以規則答（metadata 精確計算），避免 LLM 編故事。
    if brief_law is not None and an is None:
        law_meta._try_load_law_meta()
        intent = law_meta._route_law_intent(question)
        if intent:
            line = law_meta._rule_answer(intent, brief_law)
            if line:
                base = {"ok": True, "host": gateway.HOST_ID, "confidence": "rule",
                        "no_match": False, "src": src, "log": await gateway._host_probe_log()}
                base["answer"] = f"{gateway.HOST_ID}: {line}"
                base["trace"] = f"題庫:{intent}(內建)"
                return base
    # ── RAG 引擎（題庫 miss 才花 gateway.embed＋retrieve.search）──
    vecs = await gateway.embed([question])
    hits = await retrieve.search(question, vecs[0], limit=recall)
    dense = await retrieve._dense_leg(vecs[0], max(recall, 50))
    dense_max = max(dense.values(), default=0.0)
    top = retrieve.rerank(question, hits, dense, top_k)
    level, reason = retrieve._decide(question, top, dense_max, retrieve.MIN_DENSE, retrieve.MID_DENSE, retrieve.HIGH_DENSE)
    exact_n = sum(1 for h in hits if h.get("_exact_rank"))
    # 純法名查詢（無條號）→ 整部法連結；其餘（含條號/語意命中具體條文）→ 單條文連結
    law_only = bool(brief_law) and an is None
    views = [_hit_view(h, law_only=law_only) for h in top]
    base = {"ok": True, "host": gateway.HOST_ID, "confidence": level, "relevance": reason,
            "no_match": False,
            "trace": retrieve._trace(question, an, exact_n, dense_max, level, reason),
            "src": src, "hits": views,
            "log": await gateway._host_probe_log()}
    if bank_note:
        base["trace"] += bank_note
    if level == "no_match":
        base["no_match"] = True
        base["answer"] = "依目前資料沒有符合比對的法條。請換個關鍵字，或確認問題屬於法律範圍後再查詢。"
        return base
    # ⚠️ 條號精準命中 → **直接引用 top1 條文**，不問 LLM。
    #
    # 症狀（2026-10-05 實測）：問「證券交易法第15條」回的是
    #   「《證券交易法》（共209條）」—— 完全沒有第15條的內容。
    # 根因是下面那個 `brief_law` 分支：它只看「有沒有偵測到法名」與
    # 「top1 是不是那部法」，**沒有排除有條號的情況** → 「證券交易法**第15條**」
    # 被當成「查《證券交易法》這部法」處理，回簡介。而且該分支還會
    # `_strip_article_refs()` 把條號洗掉，LLM 即使答對也會被抹成一句話。
    # （trace 已經寫著 `條號:第 15 條｜精準:1篇` —— 資料早就到了，只是走錯分支。）
    #
    # 為什麼可以跳過 LLM：條號精準命中是**字面相同**的條文，不是推論。用戶問的是
    # 「這條怎麼規定」，答案就是那條的原文；讓 LLM 改寫只會有機會失真（實測
    # JEV:0.31 判定不合格而退回規則卡，等於白花一次 LLM 呼叫）。
    # 這也讓 confidence=rule 的語意一致：可逐字核實。
    #
    # ⚠️ 只在「條號與命中條文的 article_no 完全相同」時才用 —— 那個判斷由
    # retrieve._exact_match() 提供（它自己會去空白比對）。不要放寬成
    # 「有條號就答 top1」：那會讓「第15條」答成別的條文。
    if an and top and retrieve._exact_match(top, an):
        p0 = top[0].get("payload") or {}
        txt = collapse_ws(p0.get("text") or "").strip()
        if txt:
            base["answer"] = f"{gateway.HOST_ID}: {txt}"
            base["confidence"] = "rule"   # 逐字引用、非生成
            base["trace"] += f"｜條號精準→直接引用{law_meta._detect_law(question) or ''}條號{an}"
            return base
    if brief_law and an is None and top and (top[0].get("payload") or {}).get("law_name") == brief_law:
        brief = law_meta._law_brief(brief_law)
        try:
            text = await generate(question, top, cautious=level == "medium", brief_law=brief_law, model=model)
        except (httpx.ConnectError, httpx.TimeoutException):
            base["answer"] = f"{gateway.HOST_ID}: {brief}"  # LLM 掛了也要回應基本敘述
            return base
        text = law_meta._strip_article_refs(text) or brief
        # 模型若誤回「沒有符合比對的法條」等拒答（法名本身已確認存在），直接退回基本敘述，
        # 避免拼出「《公司法》（共..條）：沒有符合比對的法條」這種多餘句。
        if "沒有符合比對的法條" in text or "未收錄" in text:
            text = brief
        if not (text.startswith(f"《{brief_law}") or text.startswith(brief.split("共")[0])):
            text = f"{brief}：{text}"
        if text != brief:  # 已退回規則卡就沒 LLM 敘述可驗，直接回
            jv = await _jev_verify(question, text, top)
            if jv is not None and jv < JEV_VERIFY_MIN:
                text = brief  # 驗證不通過 → 整段退回可核實的規則卡，不讓 LLM 敘述留著編造
            if jv is not None:
                base["trace"] += f"｜JEV:{jv:.2f}{'(退回規則卡)' if jv < JEV_VERIFY_MIN else '(keep)'}"
        base["answer"] = f"{gateway.HOST_ID}: {text}"
        return base
    text = await generate(question, top, cautious=level == "medium", model=model)
    jv = await _jev_verify(question, text, top)  # 一般分支先只記錄分數，供校準閾值
    base["answer"] = f"{gateway.HOST_ID}: {text}"
    if jv is not None:
        base["trace"] += f"｜JEV:{jv:.2f}(keep)"
    return base


def _hit_view(h: dict, law_only: bool = False) -> dict:
    """前端引用渲染用的精簡欄位：語意相似度%／精準旗標／判斷值／條號／款位／來源連結／內容。
    law_only＝法名查詢（例:「證券交易法」）→ 整部法連結；否則依條號給單條文連結。"""
    p = h.get("payload", {})
    d = h.get("_dense")
    law = bool(h.get("_brief"))
    if law:
        jud = f"法名:{p.get('law_name', '')}｜{h['_brief']}"
    elif h.get("_exact_rank"):
        dstr = f"{d:.4f}" if d is not None else "n/a"
        jud = f"dense cosine:{dstr}|exact:{h['score']:.4f}"
    else:
        sp = h.get("_sparse", 0.0)
        jud = f"dense cosine:{d or 0.0:.4f}|sparse idf:{sp:.4f}|sum:{h['score']:.4f}"
    # 精準分支（條號/法名）：不給相對%語意（rel=None），前端顯示「精準/簡介」徽章
    rel = None if (law or h.get("_exact_rank")) else (None if d is None else int(round(d * 100)))
    art_no = p.get("article_no")
    view = {"score": h["score"], "payload": p, "jud": jud, "law": law,
            "rel": rel,
            "exact": bool(h.get("_exact_rank")),
            "url": law_meta._law_url(p.get("pcode"), None if law_only else art_no),
            "art": (art_no or "").replace(" ", "") or p.get("law", ""),
            "law_name": p.get("law_name", ""),
            "item": _law.cite_item(p.get("text", ""))}
    s = _law.structure(p.get("text", ""))
    view["para_count"] = s["para"]
    view["item_count"] = len(s["items"])
    return view


# ── 相容層：main.py 仍以 `rag.<名稱>` 呼叫 gateway 的函式與設定 ────────────
# 這些名字在 2026-09-29 切模組前住在 rag.py。轉出而非改 main.py，是為了讓
# 「HTTP 呼叫去 gateway、檢索去 retrieve」這件事在 import 邊界就看得出來，
# 而不是靠一層一層的轉發。
#
# ⚠️ 轉出只適合「呼叫端不 patch」的情況。要 monkeypatch 這些符號（例如測試
# 攔截 _req），必須 patch `gateway` 模組本身 —— patch `rag._req` 只會改到
# 這裡的別名，gateway 內部的呼叫看不到。tests/test_rag_engine.py 與
# tests/test_rag_public.py 都已改為 patch 擁有者模組。
from .gateway import (  # noqa: E402,F401
    HOST_API,
    LLM_MODEL,
    _gateway_token,
    _host_law_versions,
    _host_probe_log,
    active_llm_source,
    local_models,
    warmup,
)


# ── 相容層：main.py 仍以 `rag.<名稱>` 呼叫下層的函式與設定 ────────────────
# 這些名字在 2026-09-29 切模組前住在 rag.py。轉出而非改 main.py，是為了讓
# 「HTTP 呼叫去 gateway、檢索去 retrieve」這件事在 import 邊界就看得出來。
# main.py 用到 36 個 rag.* 名稱，逐一改會讓 diff 淹沒真正的分層改動。
#
# ⚠️ 轉出只適合「呼叫端不 patch」的情況。要 monkeypatch 這些符號（例如測試
# 攔截 _req），必須 patch 擁有者模組 —— patch rag._req 只會改到這裡的別名，
# gateway 內部的呼叫看不到。tests/test_rag_engine.py、test_rag_public.py、
# test_rag_pure.py 都已改為 patch 擁有者模組。
from .law_meta import builtin_catalog  # noqa: E402,F401
from .retrieve import COLLECTION, ensure_collection, upsert  # noqa: E402,F401
from .gateway import (  # noqa: E402,F401
    HOST_API,
    LLM_MODEL,
    _gateway_token,
    _host_law_versions,
    _host_probe_log,
    active_llm_source,
    local_models,
    warmup,
)


# ── 注入點：把 law_version 交給 gateway，避免 gateway → rag 循環依賴 ──────
# gateway._host_law_versions 需要本機法規版號，但 law_version 有 TTL 快取、
# 住在 rag.py。直接 import 會形成 rag → gateway → rag。
# 這裡在 import 時單向注入，維持 gateway 不依賴 rag 的分層。
gateway._LAW_VERSION_FN = law_version
