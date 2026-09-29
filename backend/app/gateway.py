"""對外連線層：主機發現、端點選取、HTTP 請求、embedding。

從 rag.py 切出來的理由：這一層和「檢索」無關，卻原本佔了 rag.py 前 400 行，
讓 1800 行的檔案看起來比實際複雜度大。它同時是唯一知道「多台 ollama／qdrant
怎麼選」的地方 —— 2026-09-27 解除三台鎖死就是改這一層。

分層規則：gateway 不 import 任何上層模組（retrieve／law_meta／rag），避免循環。
`embed` 放在這裡是刻意的 —— 它只是「又一個 ollama HTTP 呼叫」，和 local_models
同類；放在 rag 會造成 retrieve → rag → retrieve 的循環依賴。

跨模組呼叫一律走屬性存取（`gateway._req(...)`）而非 `from .gateway import _req`：
後者在 import 時就把名稱綁死在本地命名空間，測試 monkeypatch 模組屬性將不會生效。
"""
import asyncio
import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("ragdemo")

# 預設值不得是「別台機器的位址」——2026-09-27 解除三台鎖死時改掉。
# 舊預設是 x570 的 tailscale IP：一台全新的機器只要沒設 OLLAMA_*，查詢就會去戳
# 別台機器的 ollama，然後得到「連不上」而不是用自己的。
# host.docker.internal 是 Docker 慣用名：Docker Desktop 自動提供，
# Linux 需 compose 裡的 extra_hosts: ["host.docker.internal:host-gateway"]（已加）。
OLLAMA_DEFAULT = os.getenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434").rstrip("/")
QDRANT_DEFAULT = os.getenv("QDRANT_URL", "http://localhost:6333").rstrip("/")
def _split_endpoints(raw: str, fallback: str = "") -> tuple[list[str], dict[str, str]]:
    """把候選清單拆成 (純網址清單, 網址→主機標籤)。清單空時回 [fallback]。

    每段可以是純網址，也可以是 '主機id=網址'。標籤**只是給人看的**（前端
    「連線與來源」要顯示這台 ollama 是誰），不影響選取、順序或索引對應 ——
    所以 OLLAMA_MODELS 的位置對應關係不受影響。

    為什麼需要這個：`_KNOWN_IPS` 那張寫死的 IP→id 對照表就是為了讓
    `http://100.x.x.x:11434` 顯示成「x570」而存在的。刪掉它之後如果不給
    別的來源，這裡就會退化顯示裸 IP（資訊沒少，src.llm.url 仍在，但面板
    少了「這是哪台」的辨識）。把標籤交給設定檔，就不必在程式裡列舉主機。

    ⚠️ 空清單的回退**在這裡**做，不要寫成在外面 `or [那個常數]`：
    那樣那個常數會多出一個使用點，env-audit 的「純連鎖」判定（pass 3 要求
    該常數**除了定義與當預設值外沒有其他用處**）就會失效，於是
    OLLAMA_BASE_URL 不再被標成被 OLLAMA_URLS 蓋掉 —— 那條提示會從
    .env.example 與稽核輸出裡安靜消失。實測踩到。
    ⚠️ 連這段說明文字本身也不能寫出那個常數名：pass 3 是逐行字面比對，
       docstring 裡出現一次就算一次「使用」。這是同一個陷阱的第二次。
    """
    urls: list[str] = []
    labels: dict[str, str] = {}
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        hid, sep, url = part.partition("=")
        if not sep or "://" not in part:
            url = part
        url = url.strip().rstrip("/")
        if "://" not in url:
            continue
        urls.append(url)
        if sep:
            labels[url] = hid.strip()
    return (urls, labels) if urls else ([fallback] if fallback else [], labels)


OLLAMA_URLS, OLLAMA_LABELS = _split_endpoints(
    os.getenv("OLLAMA_URLS", OLLAMA_DEFAULT), OLLAMA_DEFAULT)
QDRANT_URLS, QDRANT_LABELS = _split_endpoints(
    os.getenv("QDRANT_URLS", QDRANT_DEFAULT), QDRANT_DEFAULT)
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "").strip()
EMBED_MODEL = os.getenv("EMBED_MODEL") or "bge-m3:latest"
LLM_MODEL = os.getenv("LLM_MODEL") or "qwen3:14b"
# OLLAMA_MODELS：與 OLLAMA_URLS 同順序的 LLM model 清單；未設則全部用 LLM_MODEL。
OLLAMA_MODELS = [m.strip() for m in os.getenv("OLLAMA_MODELS", LLM_MODEL).split(",") if m.strip()] or [LLM_MODEL]
CF_AIG_TOKEN = os.getenv("CF_AIG_TOKEN", "").strip()
CF_AIG_TOKEN_FILE = os.getenv("CF_AIG_TOKEN_FILE", str(Path.home() / ".config" / "opencode" / "cf-aig-token"))
# 預設空字串（與 registry.py 一致）。舊預設是 "x570"，那不只是鎖死，還是 bug：
# 一台沒設 HOST_ID 的新機器會冒用 x570 的身分，覆寫它在 registry 的條目，
# 而且 /query 會自稱 x570，與 registry 記的空字串互相矛盾。
HOST_ID = os.getenv("HOST_ID", "")
# 重新掃描優先權的間隔（秒）：降級後每 PICK_TTL 重測一次，高位主機回復就切回。
PICK_TTL = float(os.getenv("PICK_TTL") or "30")
# 模型常駐時間（ollama keep_alive）：-1=永久常駐（預設）、0=即時卸載、"30m"=30 分鐘。
KEEP_ALIVE = os.getenv("KEEP_ALIVE", "-1")
# 前端「連線與來源」彈窗的主機探測（dev 路徑；prod 由 Pages worker 自行探測後覆蓋此欄位）。
#
# 單一變數取代舊的 HOST_API_X570 / HOST_API_MBP / HOST_API_MSI：
#   HOST_API_URLS=x570=https://api-x570.ragdemo.win,msi=https://api-msi.ragdemo.win
# 純 URL（沒有 "id=" 前綴）也收，此時 id 由 hostname 推導。**未設＝單機，沒有 peer**。
# 舊的三個變數名已刪（留著就是「非必要程式碼」）；env-audit.py 會報出殘留的 HOST_API_* 鍵，
# 所以三台升級時會被叫到，不會靜默掉 peer。
def _parse_id_urls(raw: str) -> dict[str, str]:
    """解析 'id=url,id=url' 或 'url,url' → {id: url}。跳過空片段與解析不出 id 的項。"""
    out: dict[str, str] = {}
    for part in (raw or "").split(","):
        part = part.strip().rstrip("/")
        if not part:
            continue
        hid, sep, url = part.partition("=")
        if not sep or "://" not in part:
            # 純 URL：id 取 hostname 的第一段（api-x570.ragdemo.win → api-x570）
            url = part
            hid = (urlparse(url).hostname or url).split(".")[0]
        hid, url = hid.strip(), url.strip().rstrip("/")
        if hid and "://" in url:
            out[hid] = url
    return out
HOST_API = _parse_id_urls(os.getenv("HOST_API_URLS", ""))
PROBE_TIMEOUT = float(os.getenv("PROBE_TIMEOUT") or "2.5")
def keep_alive_value():
    """ollama 的 keep_alive：純數字（含 -1）要傳 number，其餘（如 "30m"）傳字串。"""
    s = str(KEEP_ALIVE).strip()
    return int(s) if s.lstrip("-").isdigit() else s
def _gateway_token() -> str:
    """CF AI Gateway token：先看 CF_AIG_TOKEN env，未設（本機 dev）讀 token 檔。"""
    if CF_AIG_TOKEN:
        return CF_AIG_TOKEN
    try:
        return Path(CF_AIG_TOKEN_FILE).read_text().strip()
    except Exception:
        return ""
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
def host_label(url: str) -> str:
    """把服務 URL 映射成主機 id（無法辨識則回 hostname，本機則回 HOST_ID）。

    舊版拿一張寫死的 IP → id 對照表（_KNOWN_IPS），那正是「鎖死三台」的東西：
    第 4 台進來就必須改程式。現在改從 HOST_API_URLS 反向建索引，所以
    「配了哪些 peer」就等於「認識哪些主機」——單一來源。
    """
    host = (urlparse(url).hostname or "").lower()
    if host in ("127.0.0.1", "localhost"):
        return HOST_ID
    for table in (OLLAMA_LABELS, QDRANT_LABELS):  # 候選清單上運營者自訂的標籤
        for u, hid in table.items():
            if (urlparse(u).hostname or "").lower() == host:
                return hid
    for hid, api in HOST_API.items():  # 從設定反推，不寫死
        if (urlparse(api).hostname or "").lower() == host:
            return hid
    if "." not in host:  # compose service 名（如 qdrant）在本機跑 → 標本機
        return HOST_ID
    return host
async def _host_probe_log() -> dict[str, str]:
    """探測 HOST_API_URLS 裡的 peer /health，回傳前端「連線與來源」的 log（與 worker 同字串）。
    worker 在 prod 會用自己探的 log 覆蓋；此函式主要服務 dev（vite proxy 直連 backend）。

    **本機永遠在 log 裡**（有回應本身就是「活著」的證據，值恆為連線成功）。
    舊版本機之所以會出現，只是因為它剛好被寫死在那三台清單裡 —— 一旦清單變成
    設定值，單機部署的 log 就會變空，前端面板會整個消失。
    """
    async def _one(url: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=PROBE_TIMEOUT, follow_redirects=True) as c:
                r = await c.get(f"{url}/health")
                return r.status_code < 500
        except Exception:
            return False
    peers = {h: u for h, u in HOST_API.items() if h != HOST_ID}
    ok = await asyncio.gather(*(_one(u) for u in peers.values()))
    out = {h: ("連線成功" if o else "連線失敗") for h, o in zip(peers, ok)}
    if HOST_ID:
        out[HOST_ID] = "連線成功"
    return out
async def _host_law_versions() -> dict[str, str]:
    """並行抓 peer（連同本機）的法規版本。回 {host_id: "2026-09-11" or "-"}。

    刻意不比照 _host_probe_log 的 `< 500` 判定：版本要的是 /status 的
    200 內容，5xx 以外的錯誤回應（反代 4xx 等）不該被當成有版本。
    """
    async def _one(url: str) -> str:
        try:
            # probe=0 一定要帶：遠端的 /status 預設會再去探測「它的」三台主機。
            # 不加這個參數就是 A→B→C→A 的遞迴，請求數會指數成長。
            async with httpx.AsyncClient(timeout=PROBE_TIMEOUT, follow_redirects=True) as c:
                r = await c.get(f"{url}/status", params={"probe": 0})
                if r.status_code != 200:
                    return "-"
                return (r.json().get("law_version") or {}).get("update_date") or "-"
        except Exception:
            return "-"
    peers = list(zip(HOST_API, await asyncio.gather(*(_one(u) for u in HOST_API.values()))))
    # 本機：HOST_API_URLS 列的是 peer，本機版號直接從磁碟取（免一次自我 HTTP）
    me = _local_law_version().get("update_date") or "-"
    pairs = {h: v for h, v in peers if h != HOST_ID}  # 別把自己重複列兩次
    return {**pairs, HOST_ID: me} if HOST_ID else pairs


def _local_law_version() -> dict:
    """本機法規版本。**注入點** —— 避免 gateway → rag 的循環依賴。

    `law_version()` 住在 rag.py（有 TTL 快取），而 rag.py 已經依賴 gateway。
    若 gateway 在這裡 `from .rag import law_version`，就形成 import 循環。
    改成由 rag.py 在 import 時把函式塞進來（見 rag.py 底部的 `_wire`），
    單獨 import gateway 時不會拉起整條 rag 依賴鏈。

    沒被注入（例如測試直接 import gateway）時回 {} —— 呼叫端
    `_host_law_versions` 已有 `or "-"` 的後備，不會因此壞掉。
    """
    fn = globals().get("_LAW_VERSION_FN")
    try:
        return fn() if fn else {}
    except Exception:
        return {}


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
    headers = dict(kw.pop("headers", {}) or {})
    if kind == "qdrant" and QDRANT_API_KEY:
        headers.setdefault("api-key", QDRANT_API_KEY)
    for _ in range(2):
        base = await _pick(kind, candidates, probe=probe)
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await getattr(c, method)(f"{base}{path}", headers=headers, **kw)
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
