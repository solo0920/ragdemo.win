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

# 收掉 usage.py 原本的 `__import__("os").getenv(...)` 寫法。
_pool = None


async def pool_get():
    global _pool
    if _pool is None:
        if asyncpg is None:
            raise RuntimeError("asyncpg 未安裝")
        _pool = await asyncpg.create_pool(POSTGRES_DSN, min_size=1, max_size=3)
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
