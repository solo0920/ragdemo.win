"""共用 asyncpg 連線池。

過去 registry.py 與 usage.py 各自維護一份 `_pool_get()`，對**同一個 DSN 開兩個池**，
而且錯誤處理不一致：

- `registry.heartbeat()` 失敗時會關掉池並清掉 global → 下次呼叫重建（自癒）。
- `usage.track()` / `usage.snapshot()` 失敗時只 swallow 例外，**不關池**。

後者是潛在 bug：PG 重啟（或連線被中間設備斷掉）之後，usage 會一直抱著死掉的池不放，
每次呼叫都立刻失敗，而且**不會自己好**，只有 process 重啟才會恢復。

這裡收成單一池，兩邊共用同一套自癒路徑（`drop_pool()`）。

池大小：原本 registry=3、usage=2。合併後取 3（較大的那個），避免 heartbeat
連續寫入時排隊。
"""
import os

try:
    import asyncpg
except ImportError:
    asyncpg = None

POSTGRES_DSN = os.getenv("POSTGRES_DSN") or "postgresql://rag@postgres:5432/ragdemo"

# 建立連線的逾時上限（秒）。asyncpg 預設 60，但**症狀不是「等 60 秒後報錯」
# 而是更難查的東西**：
#
# 2026-09-30 mbp 實測 —— DSN 指向離線的 x570 時，/query +60s、/hosts 與 /models
# 掛死 >20s。原因是 Tailscale 路徑上對端不回 RST（不像 localhost 的 refused），
# TCP connect 就一直掛著。**同樣的病不限於 pg**：任何 POSTGRES_DSN /
# OLLAMA_URLS / HOST_API_URLS 裡的位址不可達都會重演。
#
# 3 秒是因為這裡只連**本機 compose 的 pg 容器**（各台 /hosts 已改讀自己的 pg，
# 2026-09-30 實測），正常是毫秒級；3 秒足以容忍容器剛起來的慢啟動，又不會
# 讓一個離線位址拖垮整個 request。
PG_CONNECT_TIMEOUT = float(os.getenv("PG_CONNECT_TIMEOUT") or "3")

# 收掉 usage.py 原本的 `__import__("os").getenv(...)` 寫法。
_pool = None


async def pool_get():
    global _pool
    if _pool is None:
        if asyncpg is None:
            raise RuntimeError("asyncpg 未安裝")
        # connect_kwargs 是 asyncpg 轉給 connect() 的 kwargs；`timeout` 才是
        # connect 的逾時參數（create_pool 自己**沒有** timeout 參數，容易看漏）。
        _pool = await asyncpg.create_pool(
            POSTGRES_DSN, min_size=1, max_size=3,
            connect_kwargs={"timeout": PG_CONNECT_TIMEOUT},
        )
    return _pool


async def drop_pool() -> None:
    """關掉並清掉目前的池。self-heal 與 shutdown 都走這裡（失敗不拋）。"""
    global _pool
    if _pool is not None:
        try:
            await _pool.close()
        except Exception:
            pass
        _pool = None
