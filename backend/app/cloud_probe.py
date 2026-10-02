"""雲端 provider 的 catalog probe：**每個 provider 一次** catalog 呼叫。

## 為什麼是「登入時做一次」而不是讓 `/ready` 輪詢

`/ready` 每 10–30 秒被輪詢一次（負載平衡、監控），而雲端 provider 的可用性檢查
若逐模型打，會變成每輪數十上百個真實 API 呼叫 —— 那是會燒額度的（free 額度是共享
池，openrouter 50/天）。登入時做一次是**一天幾次**，量級完全不同。

所以這裡刻意做成 `POST /settings/probe-clouds`（冪等、不改任何狀態），而不是
`/ready` 的一個必要條件。`/ready` 的 cloud 項**維持 `required: false`**。

## 為什麼是「一次 catalog」而不是逐模型探

每個 provider 一次 `/models` 呼叫，回來的清單讓我們**推導**每個已設定的模型是否
可用。8 個 provider ≈ 8 次請求，卻得到**逐模型**顆粒度。

## ⚠️ 三態，不是兩態（理由與 `readiness.py` 的 ollama 項一致）

| verdict | 判準 |
|---|---|
| `up` | catalog 回 200 **且**清單解析得出來 |
| `down` | **明確失敗**：HTTP 401（key 過期）／403（gateway 設定或權限）／404（路徑不對）／其他 4xx、5xx |
| `unknown` | **探不到**：逾時、DNS 失敗、TLS 失敗、連線被拒；以及回應形狀看不懂 |
| `off` | **未設定** —— 不是故障，是「這個 provider 沒開」 |

`off` 是獨立狀態而不是塞進 `down`：實測 `zen/groq/cohere/mis` 在 wsl 上沒有 key，
若把它們算成故障，`/ready` 會為四個沒開的 provider 回 503 —— 那個面板會說
「這台有問題」，而它其實沒有。

## ⚠️ 必須複用生成路徑的 httpx client 與同一組 headers

同一條 gateway 路徑，**用 `urllib` 打會回 `403 error code: 1010`**（Cloudflare 的
瀏覽器完整性檢查攔截），**用 `httpx` 就 200**（2026-10-02 實測）。

若這裡自己從零刻一個 HTTP client，結果會是**對其實正常的 provider 報「不可用」**
—— 而那是這個專案最恨的**假陰性**：症狀是「明明設定對了卻說沒有」。

同理，gateway 那條路的路徑是 `{OPENROUTER_GATEWAY_URL}/models`，**不是**
`/api/v1/models`（gateway 只轉發 `/models`，實測 `/api/v1/models` 回 404）。

## 前綴比對規則（以及為什麼不是 `in`）

已設定清單裡的 id 與 catalog 回來的 id **格式不一致**，三家各一例：

| provider | 已設定 | catalog 回來 |
|---|---|---|
| hf | `Qwen/Qwen3.8-27B` | `Qwen/Qwen3.8-27B`（一致） |
| nv | `nvidia/nemotron-3.5-lightning-30b-a3b` | `nvidia/nemotron-3.5-lightning-30b-a3b`（一致） |
| gemini | `gemini-3.8-flash` | `models/gemini-3.8-flash`（**多一層 `models/`**） |

規則：**先整串比對；整串不中再比對「最後一段 `/` 之後的葉子名」（忽略大小寫）**。

- 不用 `in` 硬碰：`"glm-5" in "some/other/glm-5-turbo"` 會**誤判成可用** ——
  那正是假陽性，比假陰性更危險（面板說能選，選了才 404）。
- 葉子名比對會放寬成「不同 owner 但同名葉子」也算中。**這個放寬是刻意的**：
  假陰性（「明明設定對了卻說沒有」）比假陽性更難排查 —— 使用者會去檢查憑證與網路，
  而問題其實只是 catalog 少寫了 owner 前綴。所以命中時會記下是 `exact` 還是 `leaf`，
  讓前端與人看得出這個判定有多確定。
- 比對**不只看 id**：`openrouter` 的每筆還有 `canonical_slug`
  （實測 `apodex/apodex-1.1-mini:free` 的 canonical_slug 是 `apodex/apodex-1.1-mini-20261001`），
  兩者都收進候選集 —— 那是同一個模型的另一種合法寫法。

## ⚠️ 回應不得含任何憑證值

例外訊息進回應前先刮掉 DSN／token 形狀（`_scrub()`）。回應會被前端拿到、
可能貼進 issue。headers 裡的 token **只**留在 outbound request。
"""
import asyncio
import copy
import logging
import re
import time
from datetime import datetime, timezone

import httpx

from . import rag

logger = logging.getLogger("ragdemo")

# 每個 provider 的逾時。catalog 是輕量的 GET，8s 對跨雲的 TLS 交握手夠寬。
PER_PROVIDER = 8.0
# 整體上限（安全網）。各 provider 並行跑，正常情況用不到這條。
TOTAL = 20.0
# 記憶體快取：登入才觸發、實際使用量極低，但 free 額度是共享池，不該重複打。
TTL = 300.0            # 5 分鐘

UP, DOWN, UNKNOWN, OFF = "up", "down", "unknown", "off"

# 對外字串的清理。三種形狀都要刮，因為三種都真的會出現在上游錯誤訊息裡：
#   `postgresql://user:pass@host`（pg）
#   `Authorization: Bearer xxx`（CF gateway 的 Access／AI Gateway 回 401 時會原樣引用）
#   `nvapi-…` / `cfut_…` / `hf_…` / `sk-or-v1-…`（**帶廠商前綴的 key，本專案的實際格式**）
#
# 第三種是最容易漏的：它沒有 `key=` 這種上下文，是裸的一串，靠前綴認。
# 前綴與長度門檻與 CI 的「追蹤檔案不得含憑證」job 同一組（那裡也要抓到它們）。
_DSN_RE = re.compile(r"://[^\s/@:]+:[^\s/@]*@")
_TOKEN_RE = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-]{8,}")
_SECRET_KV_RE = re.compile(
    r"(?i)\b(cf[-_]?aig[-_]?authorization|authorization|x-api-key|api[-_]?key|"
    r"token|api[-_]?key)\b\s*[:=]\s*\S+")
_PREFIXED_KEY_RE = re.compile(
    r"(?i)\b(?:nvapi-|cfut_|sk-or-v1-|hf_|gsk_|xai-)[A-Za-z0-9_-]{8,}")

_cache: tuple[float, dict] | None = None
_lock = asyncio.Lock()


class Unverifiable(Exception):
    """探不到答案（逾時、連線失敗、回應形狀看不懂）。→ `verdict: unknown`。"""


def _scrub(text) -> str:
    """任何要進回應的字串都先過這裡：刮掉憑證形狀並截斷。

    ⚠️ 順序有意義：`_SECRET_KV_RE` 在 `_PREFIXED_KEY_RE` **之前**跑，
    否則 `api-key: nvapi-…` 會先被前綴規則吃掉一半，剩下的值就沒有上下文可刮了。
    """
    s = str(text)
    s = _DSN_RE.sub("://***:***@", s)
    s = _TOKEN_RE.sub(r"\1***", s)
    s = _SECRET_KV_RE.sub(r"\1=***", s)
    s = _PREFIXED_KEY_RE.sub("***", s)
    return s[:200]


# ── 已設定清單（唯一的真相來源＝ rag.py 的常數）────────────────────────
#
# ⚠️ 這裡**不**另立一份模型清單。設定在 `*_MODELS` 環境變數裡，而那份清單同時是
# 前端下拉選單的內容 —— 另立一份會漂移，而漂移的表現是「面板說某模型不可用，
# 但它明明在下拉選單裡」。

def _models_of(key: str) -> list[str]:
    return {
        "openrouter": rag.OPENROUTER_MODELS,
        "zen": rag.ZEN_FREE_MODELS,
        "nvidia": rag.NVIDIA_MODELS,
        "gemini": rag.GEMINI_MODELS,
        "groq": rag.GROQ_MODELS,
        "cohere": rag.COHERE_MODELS,
        "hf": rag.HF_MODELS,
        "mistral": rag.MISTRAL_MODELS,
    }[key]


def _cf_headers() -> dict:
    """CF AI Gateway 那一組的 headers —— **逐字複製** rag.py 的生成路徑。

    `cf-aig-authorization` 與 `Authorization` 兩個都要帶（rag.py 的
    `_openrouter_complete` / `_gemini_complete` / … 都是這一組）。
    少一個就可能是 403，而 403 在 probe 的語意裡是 `down` —— 假陰性。
    """
    tok = rag._gateway_token()
    return {
        "Content-Type": "application/json",
        "cf-aig-authorization": f"Bearer {tok}",
        "Authorization": f"Bearer {tok}",
    }


def _provider_key(key: str) -> str:
    """該 provider 的上游憑證值（**只**進 outbound header，永不進回應）。

    CF AI Gateway 那一組（openrouter/gemini/groq/cohere/mistral）的 key 由 gateway
    後台代管，所以這個位置放的是 CF token —— 與 `rag._*_complete` 認證的是同一個值。
    """
    return {
        "openrouter": rag._gateway_token(),
        "zen": rag.ZEN_API_KEY,
        "nvidia": rag.NVIDIA_API_KEY,
        "hf": rag.HF_TOKEN,
        "gemini": rag._gateway_token(),
        "groq": rag._gateway_token(),
        "cohere": rag._gateway_token(),
        "mistral": rag._gateway_token(),
    }[key]


def _bearer(key: str) -> dict:
    """該 provider 直接打上游時的 headers —— 與 `rag._*_complete` 同一組。"""
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {_provider_key(key)}",
    }


def _via_cf(url: str) -> bool:
    """CF AI Gateway 那一組的「URL 算不算設定了」—— `"-"` 是未設定的 sentinel。"""
    return bool(url and url != "-")


# ── 每個 provider 的「設定面」與「catalog 端點」 ─────────────────────────
#
# ⚠️ `configured` 的判準**必須**與 `readiness._cloud_state()` 一致（那是 `/models`
# 下拉選單的 `*_ready` 判準）。這裡不重寫一份判準：`readiness` 反過來讀這個函式，
# 兩邊共用同一份，所以不會漂移。

_PROVIDERS = ("openrouter", "zen", "nvidia", "gemini", "groq", "cohere", "hf", "mistral")

# 顯示用的前綴（前端下拉選單用的 value 前綴，不是 catalog 的 owner 前綴）
_PREFIX = {"nvidia": "nv", "mistral": "mis", "openrouter": "openrouter", "zen": "zen",
           "gemini": "gemini", "groq": "groq", "cohere": "cohere", "hf": "hf"}


def configured_providers() -> dict[str, bool]:
    """每個 provider「設定了沒有」。**單一真相** —— `readiness` 的 cloud 項也讀這份。"""
    tok = rag._gateway_token()

    def cf(url: str) -> bool:
        return _via_cf(url) and bool(tok)

    return {
        "openrouter": bool(rag.OPENROUTER_GATEWAY_URL and tok),
        "zen": bool(rag.ZEN_API_KEY),
        "nvidia": bool(rag.NVIDIA_API_KEY),
        "gemini": cf(rag.GEMINI_GATEWAY_URL),
        "groq": cf(rag.GROQ_GATEWAY_URL),
        "cohere": cf(rag.COHERE_GATEWAY_URL),
        "hf": bool(rag.HF_BASE_URL and rag.HF_TOKEN),
        "mistral": cf(rag.MISTRAL_GATEWAY_URL),
    }


def _off_reason(key: str) -> str:
    """未設定時說明缺哪一個變數 —— 讓人知道要補什麼，而不是只看到「沒開」。"""
    return {
        "openrouter": "未設定 OPENROUTER_GATEWAY_URL 或 CF_AIG_TOKEN",
        "zen": "未設定 ZEN_API_KEY",
        "nvidia": "未設定 NVIDIA_API_KEY",
        "gemini": "未設定 GEMINI_GATEWAY_URL 或 CF_AIG_TOKEN",
        "groq": "未設定 GROQ_GATEWAY_URL 或 CF_AIG_TOKEN",
        "cohere": "未設定 COHERE_GATEWAY_URL 或 CF_AIG_TOKEN",
        "hf": "未設定 HF_BASE_URL 或 HF_TOKEN",
        "mistral": "未設定 MISTRAL_GATEWAY_URL 或 CF_AIG_TOKEN",
    }[key]


def catalog_request(key: str) -> tuple[str, dict]:
    """(catalog URL, headers)。**逐字沿用生成路徑的 base URL 與 header 組合。**

    ⚠️ gateway 那組的路徑是 `{BASE}/models` —— 實測 `/api/v1/models` 回 404
    （gateway 只轉發 `/models`）。照抄上游 OpenRouter 的路徑會得到一個假的 `down`。
    ⚠️ gemini 走的是原生 `/v1beta/models`（回 `{"models":[{"name":"models/…"}]}`），
    不是 openai-compatible 的 `/models`。
    """
    if key == "openrouter":
        return f"{rag.OPENROUTER_GATEWAY_URL}/models", _cf_headers()
    if key == "zen":
        return f"{rag.ZEN_BASE_URL}/models", _bearer("zen")
    if key == "nvidia":
        return f"{rag.NVIDIA_BASE_URL}/models", _bearer("nvidia")
    if key == "gemini":
        return f"{rag.GEMINI_GATEWAY_URL}/v1beta/models", _cf_headers()
    if key == "groq":
        return f"{rag.GROQ_GATEWAY_URL}/models", _cf_headers()
    if key == "cohere":
        return f"{rag.COHERE_GATEWAY_URL}/models", _cf_headers()
    if key == "hf":
        return f"{rag.HF_BASE_URL}/models", _bearer("hf")
    if key == "mistral":
        return f"{rag.MISTRAL_GATEWAY_URL}/models", _cf_headers()
    raise KeyError(key)


# ── catalog 回應解析 ───────────────────────────────────────────────────────

def extract_ids(payload) -> set[str]:
    """從 catalog 回應抽出模型 id（與別名）。**看不懂就回空集合**（呼叫端改報 unknown）。

    兩種形狀都見過：openai-compatible 的 `{"data":[{"id":…}]}`（nv／hf／openrouter，
    實測）、gemini 原生的 `{"models":[{"name":"models/…"}]}`。

    ⚠️ **`name` 只在沒有 `id` 時才取。** openrouter 每筆同時有 `id`
    （`apodex/apodex-1.1-mini:free`）與 `name`（`Apodex: Apodex 1.1 Mini (free)`——
    那是**顯示名**，含空格與冒號）。兩個都收會讓 `count` 從 464 膨脹到 1156
    （實測），而且顯示名進比對集等於**放寬到不可能是 id 的字串** —— 那是往假陽性
    的方向放寬，而假陽性（面板說能選、選了才 404）比假陰性更危險。
    """
    if not isinstance(payload, dict):
        return set()
    out: set[str] = set()
    for bucket in ("data", "models"):
        items = payload.get(bucket)
        if not isinstance(items, list):
            continue
        for it in items:
            if isinstance(it, str):
                if it.strip():
                    out.add(it)
            elif isinstance(it, dict):
                # id 優先；只有沒有 id 時才用 name（gemini 原生只有 name）。
                ident = it.get("id") or it.get("name")
                if isinstance(ident, str) and ident.strip():
                    out.add(ident)
                # canonical_slug 是同一個模型的另一種合法寫法（openrouter 實測）。
                slug = it.get("canonical_slug")
                if isinstance(slug, str) and slug.strip():
                    out.add(slug)
    return out


def _leaf(model_id: str) -> str:
    """`models/gemini-3.8-flash` → `gemini-3.8-flash`；`a/b/c` → `c`（小寫）。"""
    return model_id.rsplit("/", 1)[-1].strip().lower()


def match_configured(configured: list[str], catalog: set[str]) -> dict:
    """把已設定清單對到 catalog 上。回 available（葉子名）/ missing / 命中方式。

    規則見模組 docstring〈前綴比對規則〉：先整串，再葉子名（忽略大小寫）。
    """
    leaves = {_leaf(m) for m in catalog}
    available: list[str] = []
    missing: list[str] = []
    how: dict[str, str] = {}
    for m in configured:
        if m in catalog:
            available.append(m)
            how[m] = "exact"
        elif _leaf(m) in leaves:
            available.append(m)
            how[m] = "leaf"
        else:
            missing.append(m)
    return {"available": available, "missing": missing, "how": how}


# ── 探測 ───────────────────────────────────────────────────────────────────

async def _probe_one(key: str) -> dict:
    """探一個 provider 的 catalog。回 (verdict, 欄位)。**不回傳憑證值**。"""
    configured = _models_of(key)
    if not configured_providers()[key]:
        # ⚠️ `missing` 刻意是**空的**：那個欄位的語意是「設定了但 catalog 上沒有」。
        # 沒設定時我們根本沒有 catalog，把它全部填滿等於**斷言**那些模型不存在 ——
        # 而那會是個假陰性（面板說「你設的 8 個 zen 模型都不存在」，而真相是
        # 「你沒開 zen」）。`configured` 仍列出那些 id，前端要自己知道未驗證。
        return {"verdict": OFF, "detail": _off_reason(key),
                "configured": list(configured), "available": [], "missing": [],
                "count": 0, "models": []}
    url, headers = catalog_request(key)
    try:
        async with httpx.AsyncClient(timeout=PER_PROVIDER) as c:
            r = await c.get(url, headers=headers)
    except asyncio.TimeoutError:
        raise Unverifiable(f"catalog 逾時（{PER_PROVIDER}s）—— 無從驗證，不等於壞了")
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout,
            httpx.RemoteProtocolError) as e:
        # 連不上 / DNS / TLS —— 「探不到」，不是「provider 壞了」。
        raise Unverifiable(f"{type(e).__name__}：{_scrub(e)}")
    except httpx.HTTPError as e:
        # 其他 httpx 錯誤（UnsupportedProtocol 等）：仍是「探不到」。
        raise Unverifiable(f"{type(e).__name__}：{_scrub(e)}")

    if r.status_code != 200:
        # 明確失敗 → down。附上上游的短訊息（已 scrub），因為 401 與 403 的處置
        # 完全不同（前者換 key，後者查 gateway 權限）。
        hint = {"401": "key 過期或無效", "403": "gateway 設定／權限不足",
                "404": "catalog 路徑不對（gateway 只轉發 /models）",
                "429": "上游限流"}.get(str(r.status_code), "")
        return {"verdict": DOWN, "count": 0, "models": [],
                "configured": list(configured), "available": [], "missing": list(configured),
                "detail": f"HTTP {r.status_code}" + (f"（{hint}）" if hint else "")}

    try:
        catalog = extract_ids(r.json())
    except Exception as e:
        raise Unverifiable(f"回應不是 JSON（{_scrub(e)}）—— 無從驗證")
    if not catalog:
        # 200 但讀不出模型：這是**形狀**問題，不是 provider 壞。報 unknown
        # 而不是 down —— 報 down 會讓人去換 key，而問題在解析。
        raise Unverifiable("HTTP 200 但解析不出模型清單（回應形狀可能變了）")

    m = match_configured(configured, catalog)
    return {"verdict": UP, "count": len(catalog), "models": sorted(catalog),
            "configured": list(configured),
            "available": m["available"], "missing": m["missing"],
            "detail": f"catalog {len(catalog)} 筆；已設定 {len(configured)} 個中 "
                      f"{len(m['available'])} 個可用"
                      + (f"，缺 {len(m['missing'])} 個" if m["missing"] else "")}


async def _probe_all() -> dict:
    tasks = {k: asyncio.create_task(_timed(k)) for k in _PROVIDERS}
    await asyncio.wait(tasks.values(), timeout=TOTAL)
    providers: dict[str, dict] = {}
    for k, t in tasks.items():
        if t.done():
            try:
                providers[k] = t.result()
            except Exception as e:      # 防護網：_timed 不該拋
                providers[k] = {"verdict": UNKNOWN, "detail": _scrub(e),
                                "configured": [], "available": [], "missing": [],
                                "count": 0, "models": []}
        else:
            t.cancel()
            providers[k] = {"verdict": UNKNOWN,
                            "detail": f"整體逾時（{TOTAL}s），無從驗證 —— 不等於壞了",
                            "configured": [], "available": [], "missing": [],
                            "count": 0, "models": []}
    # 前綴欄位讓前端不必自己維一份「key → 下拉選單前綴」對照。
    for k, v in providers.items():
        v["prefix"] = _PREFIX[k]
    return {
        "probed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "providers": providers,
        "summary": {v: sum(1 for p in providers.values() if p["verdict"] == v)
                    for v in (UP, DOWN, UNKNOWN, OFF)},
    }


async def _timed(key: str) -> dict:
    """跑一個 provider 的探測並收斂成 `{verdict, …}`。逾時 → unknown，不是 down。"""
    try:
        return await asyncio.wait_for(_probe_one(key), timeout=PER_PROVIDER)
    except (asyncio.TimeoutError, Unverifiable) as e:
        # ⚠️ 兩種 unknown 的措辭不同，但**都不是 down**：
        #   逾時 → 「探不到」；Unverifiable → 連線/DNS/TLS 失敗或形狀看不懂。
        # 措辭帶「無從驗證」是刻意的：那個面板的人要能一眼分辨「壞了」與「還不知道」。
        why = (f"catalog 逾時（{PER_PROVIDER}s），無從驗證 —— 不等於壞了"
               if isinstance(e, asyncio.TimeoutError)
               else f"無從驗證（不等於壞了）：{_scrub(e)}")
        configured = _models_of(key)
        # ⚠️ `missing` 是空的（同 _probe_one 的 OFF 分支）：沒有 catalog 就不能
        # 斷言「這些模型不存在」。「沒驗證」與「不存在」是兩件事，混淆會讓面板
        # 說出一個我們其實不知道的事。
        return {"verdict": UNKNOWN, "detail": why, "configured": list(configured),
                "available": [], "missing": [], "count": 0, "models": []}
    except Exception as e:
        configured = _models_of(key)
        return {"verdict": UNKNOWN,
                "detail": f"無從驗證（不等於壞了）：{_scrub(e)}",
                "configured": list(configured), "available": [],
                "missing": [], "count": 0, "models": []}


# ── 對外 ───────────────────────────────────────────────────────────────────

def _fresh():
    if _cache is None:
        return None
    ts, body = _cache
    return (ts, body) if time.monotonic() - ts < TTL else None


def _aged(hit) -> dict:
    """加 age，並**深拷貝**。

    深拷貝的理由與 `readiness._aged` 相同：回應會離開這個模組（給 FastAPI 序列化、
    給測試斷言），而快取是 module global。淺拷貝會讓呼叫端就地
    `body["providers"]["nv"]["verdict"] = …` 改到快取 —— 未來誰加一行「在回應上補
    個欄位」就會讓後續幾分鐘看到假資料。
    """
    ts, body = hit
    return {**copy.deepcopy(body), "age": round(time.monotonic() - ts, 1)}


def peek() -> dict | None:
    """目前有沒有 probe 結果（給 `/ready` 的 cloud 項用）。**不觸發探測**。"""
    hit = _fresh()
    return None if hit is None else _aged(hit)


def invalidate() -> None:
    global _cache
    _cache = None


async def probe(force: bool = False) -> dict:
    """探全部 provider。回 `{probed_at, providers, summary, age}`。

    `force=True` 跳過 TTL —— 「我剛換了 key，現在就探」。**只打 catalog 端點**，
    不打任何推理端點（那是會燒額度的）。
    """
    global _cache
    hit = _fresh()
    if hit is not None and not force:
        return _aged(hit)
    async with _lock:
        hit = _fresh()
        if hit is not None and not force:
            return _aged(hit)
        ts, body = time.monotonic(), await _probe_all()
        _cache = (ts, body)
        return _aged((ts, body))