"""per-host 設定（本機的預設聊天模型）：存本機 pg，網頁可設。

## 為什麼是獨立的一張表，不是 `backends.llm`

`backends` 已經有 `llm TEXT` 那一列，但它**每 30 秒被心跳覆寫**
（`registry.heartbeat()` 的 `llm=EXCLUDED.llm`）。把使用者的設定放那裡的症狀是
「設定存進去、GET 有看到、下一次心跳洗掉、沒有任何錯誤」。

獨立表的理由不是風格而是**語意**：

- `backends` 的每列 = 一個活著的 peer，**由心跳建立、由心跳更新**。它描述的是
  「peer's 自我回報」，不是本機的使用者設定。
- `default_model` 是「本機的設定」，不該要求那台先心跳過才有地方放，也不該被
  遠端可見的自我回報機制決定。
- 有了獨立表，「心跳絕不會碰到它」這件事**結構上成立**（`registry.py` 的 SQL
  只提 `backends`），而不是靠「我記得 UPDATE 不要帶這一列」。
  `tests/test_default_model.py` 把這個不變量釘住（SQL 層與原始碼層各一條）。

## 查詢時的優先權

    /query 的 model 欄位  →  這裡存的 default_model  →  gateway.LLM_MODEL

實作上 `resolve()` 回的是「**沒指定**」的空字串而不是 `LLM_MODEL`，
因為空字串讓 `rag.generate()` 走 `gateway._llm_model_for(base)` —— 那是
「該台 ollama 對應的模型」（多台 ollama + `OLLAMA_MODELS` 時不等於 `LLM_MODEL`）。
把 `LLM_MODEL` 塞進去會**改掉**未設定時的既有行為（多機部署時是靜默劣化）。

## 快取與降級

- 讀一次 DB 後快取 `TTL` 秒（查詢路徑不該每次都打 DB）。
- 寫入成功後**立刻**更新快取，不必等 TTL（使用者設完馬上生效）。
- DB 讀不到**不拋**：有上次的值就用它，沒有才回 ""（呼叫端因此回到 `LLM_MODEL`）。
  這個開關不該成為新的故障點 —— 查詢因為設定表故障而失敗是不能接受的。
- 失敗的結果只快取 `TTL_FAIL` 秒（比 TTL 短）：否則 pg 掛掉時每個查詢都要
  等一次 connect timeout（`PG_CONNECT_TIMEOUT`，預設 3s）。

刻意**不**新增環境變數（TTL 是模組常數）：設定表壞掉時不該還要靠 env 才知道
怎麼降級，而且新增變數就得同時補 `compose.yaml` 與 `.env.example`（見
backend/DESIGN.md〈陷阱〉「程式讀得到 ≠ 容器拿得到」）。
"""
import asyncio
import logging
import time

from . import gateway, registry
from .common import pg

logger = logging.getLogger("ragdemo")

# 讀到 DB 的快取壽命（秒）。夠短到「改了設定不用重啟就會生效」，
# 夠長到「每個查詢不會因此多打一次 DB」。
TTL = 10.0
# DB 不可用時的重試間隔（秒）。刻意**比 TTL 短**：這筆快取的值是降級結果
# 而不是真值，pg 恢復後要能快點接回來。
TTL_FAIL = 5.0
# 模型名長度上限。純粹是不讓一個 10KB 的字串被寫進 pg 當模型名。
MAX_LEN = 200

DDL = """
CREATE TABLE IF NOT EXISTS host_settings (
    host_id       TEXT PRIMARY KEY,
    default_model TEXT NOT NULL DEFAULT '',
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

SELECT_SQL = "SELECT default_model FROM host_settings WHERE host_id = $1"
UPSERT_SQL = """
INSERT INTO host_settings (host_id, default_model) VALUES ($1,$2)
ON CONFLICT (host_id) DO UPDATE SET
  default_model = EXCLUDED.default_model, updated_at = now()
"""
DELETE_SQL = "DELETE FROM host_settings WHERE host_id = $1"

# (monotonic 時間戳, 值, 是否為降級值)。None ＝ 還沒讀過；
# ("", False) ＝ 讀過、確定未設定 —— 兩者不可混為一談（否則每次都會打 DB）。
#
# TTL 不存進這裡而是查的時候讀常數：`TTL`／`TTL_FAIL` 才會是「旋鈕」
# （測試要能把 TTL 調成 0 來驗過期重讀），而不是寫進快取就凍結的常數。
_cache: tuple[float, str, bool] | None = None
_lock = asyncio.Lock()


def normalize(raw: str | None) -> str:
    """使用者輸入 → 儲存值。`None`／空字串／全空白 ＝ 清除（＝未設定）。"""
    return (raw or "").strip()


def envelope(model: str) -> dict:
    """GET／PUT 共用的回應形狀（前端契約見 backend/DESIGN.md〈per-host 設定〉）。

    `model` ＝ 存的設定（null 表示未設定）；`effective` ＝ 實際會用的那個
    （存的設定，沒有就用 `LLM_MODEL`）。

    **刻意沒有 `host` 欄位**：這個端點只設定自己這台。三台各有自己的 pg，
    跨機寫入等於要一套「遠端寫入授權」，而需求不需要 —— 前端直接用
    `HOST_API_URLS` 去問／寫每一台就好。
    """
    return {
        "ok": True,
        "host": registry.HOST_ID,
        "model": model or None,
        "effective": model or gateway.LLM_MODEL,
    }


async def resolve(requested: str = "") -> str:
    """查詢路徑用：`/query` 指定的 model → 存的 default_model → ""（未指定）。

    回 ""（而不是 `LLM_MODEL`）是刻意的，理由見模組 docstring：
    空字串讓 `rag.generate()` 走 `gateway._llm_model_for(base)`，
    也就是「該台 ollama 對應的模型」，維持未設定時的既有行為。

    **這個函式不得拋出** —— 預設模型的開關不該讓查詢失敗。
    """
    if requested:
        return requested
    try:
        return await stored()
    except Exception as e:  # pragma: no cover —— stored() 本身已吞掉，這是第二道
        logger.warning("default_model 解析失敗，回退未指定：%s", e)
        return ""


def invalidate() -> None:
    """讓快取過期（下次讀一定回 DB），**但保留值**。

    保留是刻意的：pg 恰好在 invalidate 之後掛掉時，降級該回到「上次已知的值」
    （使用者剛剛設的東西），而不是變成「未設定」—— 那會讓設定無聲消失。
    """
    global _cache
    if _cache is not None:
        _cache = (0.0, _cache[1], _cache[2])


def _fresh() -> str | None:
    """快取仍新鮮就回其值（含 "" ＝ 已確認未設定）；否則回 None（＝要重讀）。"""
    if _cache is None:
        return None
    ts, val, degraded = _cache
    return val if time.monotonic() - ts < (TTL_FAIL if degraded else TTL) else None


async def stored() -> str:
    """本機的 default_model（"" ＝ 未設定）。**不拋** —— 讀不到就降級。"""
    global _cache
    hit = _fresh()
    if hit is not None:
        return hit
    async with _lock:
        hit = _fresh()  # 等鎖期間可能已經有人讀過
        if hit is not None:
            return hit
        prev = _cache[1] if _cache is not None else None
        try:
            row = await _fetchrow(SELECT_SQL)
        except Exception as e:
            val = prev or ""
            _cache = (time.monotonic(), val, True)  # 降級值：TTL_FAIL 內不重試
            # 只在**狀態改變**時記一次，否則 pg 掛著時每 TTL_FAIL 秒洗一行。
            #
            # ⚠️ 這裡**刻意不** `pg.drop_pool()`：死掉的池 registry 心跳在
            #    REGISTRY_HEARTBEAT 秒內就會丟掉（自癒），而查詢路徑丟池會影響
            #    同池上正在進行中的 usage/heartbeat。降級本身已經不痛了，
            #    沒必要為「快 30 秒恢復」去動共用資源的生命週期。
            if prev != val:
                logger.warning("default_model 讀取失敗，暫用 %s（%ss 後重試）：%s",
                               val or "LLM_MODEL（未設定）", TTL_FAIL, e)
            return val
        val = normalize(row["default_model"] if row else "")
        _cache = (time.monotonic(), val, False)
        return val


async def set_default(model: str | None) -> str:
    """寫入（或清除）本機的 default_model，回傳寫入後的值。失敗**會拋**。

    寫入端點必須回報失敗（503），不能假裝成功 —— 只在**讀取**端降級。
    成功後立刻更新快取，使用者設完馬上生效，不必等 TTL。
    """
    global _cache
    val = normalize(model)
    if len(val) > MAX_LEN:
        raise ValueError(f"模型名稱過長（上限 {MAX_LEN} 字元）")
    if val:
        await _exec(UPSERT_SQL, registry.HOST_ID, val)
    else:
        await _exec(DELETE_SQL, registry.HOST_ID)
    _cache = (time.monotonic(), val, False)
    return val


# ── pg 存取：走 registry/usage 共用的那個池（common/pg.py），不自己開連線 ──
# 這兩個是唯一的資料庫接觸點，測試只要換掉它們就能自造 fixture。

async def _fetchrow(sql: str):
    pool = await pg.pool_get()
    async with pool.acquire() as con:
        await con.execute(DDL)
        return await con.fetchrow(sql, registry.HOST_ID)


async def _exec(sql: str, *args):
    pool = await pg.pool_get()
    async with pool.acquire() as con:
        await con.execute(DDL)
        return await con.execute(sql, *args)