"""`GET /ready` —— 「這台現在能不能服務一次查詢」，不是「進程活著嗎」。

## 為什麼要有這個（2026-10-02 實測，不是假設）

刪掉 `OLLAMA_URLS` 之後 `/health` **全程回 200**，而 `POST /query` 全部 500：

    httpx.ConnectError: ollama unreachable      掛在 **/api/embed**

`EMBED_MODEL=bge-m3:latest` 也是 ollama 模型，所以**每個查詢都要先算嵌入** ——
沒有 ollama 位址 = 整條 RAG 斷掉，而 `/health` 一點忙都沒幫上。是後來手動打了
一次 `/query` 才發現的。（`.githooks/pre-push` 的 HTTP smoke 抓得到，但它需要
`RAGDEMO_SMOKE=1` 才會跑。）

## 為什麼 `/health` 不改成會探（**不要動它**）

`/health` 的語意被兩個地方依賴，而它們要的是「進程活著且可达」：

1. `frontend/src/routes/api/[...path]/+server.ts` 的同儕連線探測 —— 用它決定面板上
   顯示「連線成功」還是「連線失敗」，也驗證 Cloudflare Access 的 Service Token
   有沒有生效。
2. `scripts/wait-stack.sh` —— 等 stack 就緒。

而且那個檔案上方就記錄過同款 bug：「面板說『連線成功』但查詢全 403」—— 舊版只檢查
`r.ok`，而 Cloudflare Access 回 302 時 fetch 跟到登入頁拿到 200 + text/html，於是
面板說這台活著、實際上真正的查詢會被擋。

**若 `/health` 在 ollama 掛掉時回 503，那個面板就會說「連線失敗」，而後端其實好好
地活著、只是其中一個依賴沒了。** 那正是這個專案反覆在修的那類錯誤（把「部分依賴
壞」呈現成「這台死了」）。

分工於是：`/health` = liveness（永遠 200、零探測）／`/ready` = readiness（真的探）。
`/status?probe=1` 探的是**別的主機**；`/ready` 只探自己這台的依賴鏈。兩者不合併。

## 「探不到」與「壞了」是兩件事

這條對這個專案特別重要，因為**「無從驗證」的症狀最容易被誤讀成「依賴壞了」**，然後
排查被帶去錯的方向（2026-10-02 就是這樣：`/query` 500 與「ollama 壞了」其實無關）。

所以每一項檢查回三態：

| `verdict` | 意思 | `ok` |
|---|---|---|
| `up` | 探到了，而且正常 | `true` |
| `down` | 探到了，而且**不正常** | `false` |
| `unknown` | **無從驗證**（探測逾時、回應形狀看不懂） | `false` |

HTTP 狀態碼回答的是「現在該不該把流量送來」→ 任一**必要**依賴不是 `up` 就 503
（含 `unknown`：不能驗證就不能承諾）。`verdict` 回答的是「到底是什麼狀況」→
面板與人從那裡分辨「壞了」與「還不知道」。兩者刻意分開，不合成一個總開關。
"""
import asyncio
import copy
import logging
import os
import re
import time

from . import cloud_probe, gateway, host_settings, rag, registry, retrieve
from .common import pg

logger = logging.getLogger("ragdemo")

# 結果快取：/ready 會被輪詢（負載平衡、監控、面板），不該每次都打三個相依服務。
TTL = 15.0
# 每項檢查的逾時上限。3–5s：足以容忍容器剛起來的慢啟動，又不會讓輪詢者乾等。
PER_CHECK = 5.0

# 檢索集合的**點數門檻**（`QDRANT_MIN_POINTS`，可由 .env 覆寫）。
# 預設 1：只抓「空／壞」。抓「少了一截」是 host-doctor 的 `ch_law_version` 的事 ——
# 那邊有 law_version 與 synced_at 可比，這個模組沒有。
# ⚠️ 讀取時不容錯：寫壞的值不該讓整個 readiness 在 import 時就死掉，那會讓
#   「設定寫錯」變成「服務起不來」，症狀完全對不上原因。
# 檢索集合的**點數門檻**。預設 1：只抓「空／壞」。
#
# 抓「少了一截」（例如重建到一半）是 host-doctor 的 `ch_law_version` 的事 ——
# 那邊有 law_version 與 synced_at 可比，這個模組沒有。分工要清楚，否則兩邊
# 都做一半、兩邊都抓不到。
#
# ⚠️ **刻意不是環境變數**（2026-10-03 實測踩過）：先做成了
#   `QDRANT_MIN_POINTS`，而 `env-audit.py` 反查原始碼時只認
#   `NAME = os.getenv("NAME", 預設)` 那一形（`PY_IDENT_ENV`），所以它把這個
#   判成「**必填**」寫進 `.env.example`，還附上一句
#   「⚠️ compose 沒列入 environment，**這裡設的值到不了容器**」——
#   而那第二句是**真的**：設定了也不生效。那是主動誤導人設一個沒用的變數。
#   門檻不需要按機器調（1 = 抓空），所以用模組常值。
#   日後真的要調，必須**連 compose 的 environment 一起加**，否則重演上面。
QDRANT_MIN_POINTS = 1
# 整體上限（安全網）。各項已各自有 PER_CHECK 且並行跑，正常情況用不到這條。
TOTAL = 15.0
# 回應裡列「該機有的模型」時最多顯示幾個 —— /ready 的 body 不該無上限。
MODEL_PREVIEW = 8

# 哪些是「必要」依賴（壞了就不該接流量）。`cloud` 不在內：它只是設定狀態報告，
# 而且 ollama 才是預設路徑 —— 沒有開雲端 provider 不是故障。
REQUIRED = {"postgres": True, "qdrant": True, "ollama": True, "cloud": False}

UP, DOWN, UNKNOWN = "up", "down", "unknown"

# asyncpg／httpx 的錯誤字串可能帶出 DSN（`postgresql://rag:<密碼>@postgres`）。
# /ready 的回應會被面板與監控抓走、可能貼進 issue，所以任何對外文字都先刮掉憑證。
_DSN_RE = re.compile(r"://[^\s/@:]+:[^\s/@]*@")

_cache: tuple[float, dict] | None = None
_lock = asyncio.Lock()


class Unverifiable(Exception):
    """探測給不出答案（逾時、回應看不懂）。**不等於壞了** —— 對應 `verdict: unknown`。"""


def _scrub(e) -> str:
    """例外 → 對外字串：刮掉 DSN 裡的憑證，並截斷。"""
    return _DSN_RE.sub("://***:***@", str(e))[:200]


def _preview(names) -> str:
    s = sorted(names)
    if not s:
        return "（沒有）"
    head = "、".join(s[:MODEL_PREVIEW])
    return head + (f" …共 {len(s)} 個" if len(s) > MODEL_PREVIEW else "")


# ── 四項檢查 ───────────────────────────────────────────────────────────────

async def _check_postgres() -> dict:
    """registry（backends）、host_settings 與 laws 表都在這裡。

    `SELECT 1` 是最便宜的真實往返 —— 不開新連線，走共用池（common/pg.py）。
    """
    pool = await pg.pool_get()
    async with pool.acquire() as con:
        got = await con.fetchval("SELECT 1")
    if got != 1:
        # 不是「壞了」也不是「通了」：那表示我們其實不理解自己連到了什麼。無從驗證。
        raise Unverifiable(f"SELECT 1 回 {got!r}（不是 1）—— 無從驗證")
    return {"detail": "SELECT 1 ok"}


async def _check_qdrant() -> dict:
    """檢索全靠它。用 `gateway._req`（自帶認證 header 與候選降級），並確認
    `retrieve.COLLECTION` **真的讀得到、而且裡面真的有東西**。

    ⚠️ **存在性不等於內容**（2026-10-03 加點數門檻，因為踩到真實事故）：
    這裡原本只驗「可讀」，而 `qdrant_load` 的流程是**先刪掉本機集合再重建** ——
    中途被中斷就留下一個**存在但空**的集合，`GET /collections/laws` 照樣回 200，
    readiness 因此報綠。實測：備援機的laws 歸零（39879 → 0）、
    `/query` 回 `hits=0 no_match=true`，而 `/ready` 說
    `collection laws 可讀`、`ok=True`。**那個狀態只有問點數才看得出來。**

    門檻刻意預設 1，不抓「少了一截」：那要看**上次同步了多少**，
    而這個模組沒有那個狀態 —— 那是 host-doctor 的
    `ch_law_version`（它有 law_version 與 synced_at 可比）。分工要清楚，
    否則兩邊都做一半、兩邊都抓不到。
    """
    coll = retrieve.COLLECTION
    r = await gateway._req("qdrant", gateway.QDRANT_URLS, "get",
                           f"/collections/{coll}", timeout=PER_CHECK)
    if r.status_code == 404:
        raise RuntimeError(f"collection {coll!r} 不存在（檢索會全滅）")
    if r.status_code != 200:
        raise RuntimeError(f"GET /collections/{coll} 回 {r.status_code}")
    try:
        res = (r.json() or {}).get("result") or {}
    except Exception as e:                       # noqa: BLE001 —— 任何解析失敗都算無從驗證
        raise Unverifiable(f"GET /collections/{coll} 回的不是 JSON（{type(e).__name__}）—— 無從驗證點數")
    n = res.get("points_count")
    if n is None:
        n = res.get("vectors_count")             # qdrant 舊版的欄位名
    if n is None:
        raise Unverifiable(
            f"回應裡沒有 points_count／vectors_count —— 無從驗證點數。"
            f"**不要**因為讀不到就當通過：空集合正是這個形狀。"
            f"（result 的欄位：{sorted(res)[:8]}）")
    if n < QDRANT_MIN_POINTS:
        # ⚠️ 訊息要**區分兩種成因** —— 否則它會自己說謊：門檻被設成一個
        #   不可能的數字時，集合有 39880 點卻被說成「裡面沒東西」，讀表的人
        #   會去查資料而真正的原因（門檻寫錯）沒人查。
        why = ("**集合是空的**" if n == 0 else
               f"只有 {n} 點、門檻 {QDRANT_MIN_POINTS} —— 可能是重建到一半"
               f"（`qdrant_load` 的流程是先刪掉本機集合），也可能是門檻本身設錯了")
        raise RuntimeError(
            f"collection {coll!r} 點數不足：{n} < {QDRANT_MIN_POINTS} —— {why}。"
            f"檢索會全滅。")
    return {"detail": f"collection {coll} 可讀，{n} 點（門檻 {QDRANT_MIN_POINTS}）"}


async def _ollama_candidate(url: str, dm: str) -> tuple[bool, str]:
    """這台 ollama 能不能服務「本機實際會用到的」那組模型。回 (ok, 失敗原因)。

    `gateway._ollama_probe()` 是主閘門 —— **複用它**而不是自己重寫，因為
    「TCP 通 ＋ 該機的聊天模型與 EMBED_MODEL 齊備」這條判準已經被降級鏈
    （`gateway._pick`）依賴；readiness 必須與它一致，否則會出現「查得了但
    /ready 說不行」或反過來。

    ⚠️ **它驗的聊天模型是 `_llm_model_for(url)`，而 `default_model` 存在時實際會用的是
    `default_model`。** 所以只要兩者不同，就必須自己再查一次 `/api/tags`：

    - probe **通過**也要查 —— probe 通過不代表 `default_model` 在那台機器上，
      而那正是要抓的形狀（`/api/generate` 吃 **404**，不是連不上，症狀完全不可見）。
    - probe **失敗**也要查 —— 該台可能沒有 `_llm_model_for` 卻有 `default_model`，
      那時查詢是成功的；直接沿用 probe 會在這裡**誤報 not ready**，而那正是
      `default_model` 這個功能要支援的情境。

    為什麼要指名缺哪一個：只說「探測失敗」的排查價值是零 —— 換模型與換機器是
    兩個完全不同的動作。
    """
    chat = dm or gateway._llm_model_for(url)
    probe_ok = await gateway._ollama_probe(url)
    if probe_ok and not dm:
        return True, ""                       # probe 已驗過 EMBED_MODEL ＋ 聊天模型
    if not probe_ok and not await gateway._tcp_open(url):
        return False, "連不上"               # TCP 都不通，讀 tags 沒意義
    if probe_ok and chat == gateway._llm_model_for(url):
        return True, ""                       # probe 驗的就是它
    try:
        have = await gateway._ollama_tags(url)
    except Exception as e:
        return False, f"讀不到 /api/tags（{type(e).__name__}）"
    # 查詢實際會用到的：永遠要 EMBED_MODEL（每個查詢都先算嵌入），加上聊天模型。
    missing = sorted({gateway.EMBED_MODEL, chat} - have)
    if missing:
        return False, f"缺 {'、'.join(missing)}（該機有: {_preview(have)}）"
    return True, ""


async def _check_ollama() -> dict:
    """聊天 + **嵌入**。ollama 掛掉 = 整條 RAG 斷掉（每個查詢都要先算嵌入）。

    `default_model` 的檢查放在**這一層**（組裝層）而不是塞進
    `gateway._ollama_probe`：gateway 是最底層，不能讀 pg，不知道 `default_model`
    的存在 —— 而要把它放進底層就得讓底層依賴資料庫，那會把分層打破。
    """
    urls = gateway.OLLAMA_URLS
    stored = await host_settings.stored()      # 不拋：讀不到回 ""（降級成 LLM_MODEL）
    # 雲端模型不在 ollama 這裡找（`openrouter/…` 之類永遠不會出現在 /api/tags）。
    dm = stored if stored and not rag.is_cloud_model(stored) else ""
    results = await asyncio.gather(*(_ollama_candidate(u, dm) for u in urls))
    for url, (ok, _) in zip(urls, results):
        if ok:
            return {"detail": f"{gateway.host_label(url)} 可服務（TCP ＋ 嵌入 ＋ 聊天模型齊備）"}
    raise RuntimeError("；".join(f"{gateway.host_label(u)}: {why}" for u, (_, why) in zip(urls, results))
                       or "沒有任何 OLLAMA_URLS —— 每個查詢都會卡在 /api/embed")


def _cloud_state() -> dict[str, bool]:
    """每個雲端 provider「設定了沒有」—— 轉發到 `cloud_probe`，**不另立一套**。

    ⚠️ 這裡**刻意**只剩一個真身。`_check_cloud()` 與 `/models` 的 `*_ready` 判準、
    以及 probe 的「這個 provider 要不要探」必須是同一件事 —— 三處各寫一份判準時，
    漂移的表現是「面板說某 provider 沒開，但它明明在清單裡」。
    """
    return cloud_probe.configured_providers()


async def _check_cloud() -> dict:
    """雲端 provider：**兩個面向分開報**。

    - **設定面**（`configured`）＝ 有幾個 provider 設了 key／URL。判準直接讀
      `cloud_probe.configured_providers()`，**不另立一套** —— 那是 `/models` 下拉選單
      的 `*_ready` 判準，兩邊共用一份才不會漂移。
    - **驗證面**（`probe`）＝ 最近一次 `POST /settings/probe-clouds` 的結果，含
      探測時間與 `stale` 旗標。**沒有結果時 `verdict: unknown`，不是 down** ——
      「還沒探過」與「探了發現壞了」是兩件事。

    ⚠️ **這裡不發請求。** `/ready` 會被每 10–30 秒輪詢，而雲端 probe 是每 provider
    一個真實 API 呼叫（free 額度共享池，openrouter 50/天）。所以驗證面是**登入時**
    由前端觸發（`POST /settings/probe-clouds`），`/ready` 只讀結果。

    未設定雲端 provider **不是故障** —— 那是「這台沒開雲端選項」，報錯會把正常
    狀況講成故障。雲端真的壞了由 `/query` 個別回報，那才是它該出現的地方。
    """
    on = [k for k, v in cloud_probe.configured_providers().items() if v]
    out = {
        "configured": on,
        "configured_count": len(on),
        "detail": (f"已設定 {len(on)} 個雲端 provider" if on
                   else "未設定任何雲端 provider（不是故障）"),
    }
    snap = cloud_probe.peek()
    if snap is None:
        out["probe"] = {"verdict": UNKNOWN, "probed_at": None, "stale": True,
                        "detail": "尚未 probe（登入時會自動觸發）"}
        return out
    verdicts = {k: p["verdict"] for k, p in snap["providers"].items()
                if k in on}
    # 驗證面的總 verdict：任一 down 就是 down；只有 unknown 則 unknown。
    if any(v == DOWN for v in verdicts.values()):
        v = DOWN
    elif any(v == UNKNOWN for v in verdicts.values()):
        v = UNKNOWN
    else:
        v = UP
    out["probe"] = {
        "verdict": v,
        "probed_at": snap["probed_at"],
        "age": snap["age"],
        "stale": snap["age"] >= cloud_probe.TTL,
        "summary": snap["summary"],
        "providers": {k: {"verdict": p["verdict"], "detail": p["detail"],
                          "count": p["count"],
                          "available": len(p["available"]), "missing": len(p["missing"])}
                      for k, p in snap["providers"].items() if k in on},
    }
    return out


_CHECKS = {"postgres": _check_postgres, "qdrant": _check_qdrant,
           "ollama": _check_ollama, "cloud": _check_cloud}


# ── 組裝 ───────────────────────────────────────────────────────────────────

async def _timed(name: str, fn) -> dict:
    """跑一項檢查並把三態收斂成 `{ok, verdict, detail}`。

    `ok` 的語意是「這一項讓不讓人安心」，`verdict` 的語意是「到底怎麼了」——
    分開是為了讓「探不到」不會被讀成「壞了」（見模組 docstring）。
    """
    try:
        out = await asyncio.wait_for(fn(), timeout=PER_CHECK)
    except (asyncio.TimeoutError, Unverifiable) as e:
        why = f"探測逾時（{PER_CHECK}s），無從驗證 —— 不等於壞了" if isinstance(e, asyncio.TimeoutError) \
            else _scrub(e)
        return {"ok": False, "verdict": UNKNOWN, "detail": why}
    except Exception as e:
        return {"ok": False, "verdict": DOWN, "detail": _scrub(e)}
    res = dict(out or {})
    res.setdefault("ok", True)
    res.setdefault("verdict", UP)
    res.setdefault("detail", "")
    return res


async def _probe_all() -> dict:
    """並行探全部依賴，順手套上整體逾時（TOTAL）這道安全網。"""
    tasks = {n: asyncio.create_task(_timed(n, f)) for n, f in _CHECKS.items()}
    done, pending = await asyncio.wait(tasks.values(), timeout=TOTAL)
    checks: dict[str, dict] = {}
    for n, t in tasks.items():
        if t in done:
            try:
                c = t.result()
            except Exception as e:      # _timed 不該拋，這裡是防護網
                c = {"ok": False, "verdict": DOWN, "detail": _scrub(e)}
        else:
            t.cancel()
            c = {"ok": False, "verdict": UNKNOWN,
                 "detail": f"整體逾時（{TOTAL}s），無從驗證 —— 不等於壞了"}
        c["required"] = REQUIRED[n]
        if not REQUIRED[n]:
            # 非必要依賴不影響 503（雲端沒開不是故障）。
            c["ok"] = True
        checks[n] = c
    stored = await host_settings.stored()
    return {
        "ok": all(c["ok"] for c in checks.values()),
        "host": registry.HOST_ID,
        # 「實際會用的模型」＝ 存的 default_model，沒設就用 LLM_MODEL —— 與
        # host_settings.envelope() 的 effective 同一條規則。
        "default_model": stored or gateway.LLM_MODEL,
        "stored": stored or None,
        "checks": checks,
    }


def _fresh():
    if _cache is None:
        return None
    ts, body = _cache
    return (ts, body) if time.monotonic() - ts < TTL else None


def _aged(hit) -> dict:
    """加上 age（這份結果是多久前探出來的）。

    **深拷貝**：回應會離開這個模組（給 FastAPI 序列化、給測試斷言），而快取是
    module global。若只淺拷貝，呼叫端就地 `body["checks"][x]["ok"] = …` 會改到
    快取 —— 未來誰加一行「在回應上補個欄位」就會讓後續 15 秒的呼叫看到假資料。
    """
    ts, body = hit
    return {**copy.deepcopy(body), "age": round(time.monotonic() - ts, 1)}


def invalidate() -> None:
    """清掉結果快取（測試與「我剛修好但還是不 ready」的排查用）。"""
    global _cache
    _cache = None


async def report(force: bool = False) -> dict:
    """一次完整的 readiness 報告（回應形狀見 main.py 的 `/ready`）。

    `force=True` 跳過 TTL 快取 —— `/ready?force=1`。沒有它，「剛把 ollama 修好」
    的人得等最多 15 秒才會看到自己修好了，然後會以為沒修好。
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