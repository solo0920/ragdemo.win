"""模型用量統計：每 provider/model 一日一列（calls、tokens）。

做法：各 _*_complete 成功取得 LLM 回應後，由 rag.py 提取該 provider 自己
的用量欄位（openai-compatible 的 usage.total_tokens、gemini 的
usageMetadata.totalTokenCount、cohere 的 usage、ollama 的
prompt_eval_count+eval_count，各家格式不同，取得到就記、取不到 tokens 記 0，
calls 一定記）。前端在選單 model 名後顯示「今日 N 次／約 T tokens」。
寫入失敗只吞掉不影響 query（best-effort）。
"""
try:
    import asyncpg
except ImportError:
    asyncpg = None

POSTGRES_DSN = __import__("os").getenv(
    "POSTGRES_DSN", "postgresql://rag@postgres:5432/ragdemo"
)

DDL = """
CREATE TABLE IF NOT EXISTS model_usage (
    provider TEXT NOT NULL,
    model    TEXT NOT NULL,
    day      DATE NOT NULL,
    calls    INT NOT NULL DEFAULT 0,
    tokens   BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (provider, model, day)
);
"""

_pool = None


async def _pool_get():
    global _pool
    if _pool is None:
        if asyncpg is None:
            raise RuntimeError("asyncpg 未安裝")
        _pool = await asyncpg.create_pool(POSTGRES_DSN, min_size=1, max_size=2)
    return _pool


async def track(provider: str, model: str, tokens: int = 0) -> None:
    """best-effort 記錄一次成功呼叫；任何失敗（PG 掛/連不上）都吞掉。"""
    try:
        pool = await _pool_get()
        async with pool.acquire() as con:
            await con.execute(DDL)
            await con.execute(
                """
                INSERT INTO model_usage (provider, model, day, calls, tokens)
                VALUES ($1, $2, now()::date, 1, $3)
                ON CONFLICT (provider, model, day)
                DO UPDATE SET calls = model_usage.calls + 1,
                              tokens = model_usage.tokens + EXCLUDED.tokens
                """,
                provider, model, int(tokens or 0),
            )
    except Exception:
        pass


async def snapshot() -> list[dict]:
    """今日各 (provider, model) 的累計 calls / tokens（排序：tokens 多→少）。"""
    try:
        pool = await _pool_get()
        async with pool.acquire() as con:
            await con.execute(DDL)
            rows = await con.fetch(
                """
                SELECT provider, model, calls, tokens
                FROM model_usage
                WHERE day = now()::date
                ORDER BY tokens DESC, calls DESC
                """
            )
        return [dict(r) for r in rows]
    except Exception:
        return []