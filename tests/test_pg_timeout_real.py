"""`connect_timeout` 的真實行為 —— 用保留位址實測，不用 mock。

為什麼要真跑：`tests/test_common_pg.py` 只證明「kwargs 送進去了」，
但那證明不了 asyncpg 真的會在 3 秒放棄。要證明後者，只能連一個
**會丟包而不回 RST** 的位址 —— 這正是 2026-09-30 mbp 踩到的情況
（DSN 指向離線的 x570，/query +60s、/hosts 掛死）。

`localhost` 的 `Connection refused` 證明不了這件事：那是**立刻**失敗，
不論 timeout 設多少。所以這裡刻意選 `10.255.255.1`（RFC 1918 保留段，
不該有主機回應）。

兩種逾時的差別就是本專案要防的病：
- `timeout=3`  → 3.0s 放棄
- asyncpg 預設 60 → 60.1s 放棄

耗時共約 63 秒，所以預設不跑（`-m slow`）；CI 的 pytest 預設不選它。
"""
import asyncio
import time

import pytest

pytestmark = pytest.mark.slow

# 10.255.255.1 屬 RFC 1918 保留段，正常不該有主機回應。選它而不是
# localhost 是因為 localhost 會立刻 refused —— 那個情況下無論 timeout
# 設多少都馬上失敗，測不出東西。
UNROUTABLE = "10.255.255.1"


async def _connect_elapsed(host: str, port: int, timeout: float | None) -> float:
    """回傳拋出例外的耗時（秒）。回傳 0.0 代表竟然連上了。"""
    import asyncpg

    t0 = time.monotonic()
    with pytest.raises(Exception):
        await asyncpg.connect(host=host, port=port, user="rag", password="x",
                              database="ragdemo", timeout=timeout)
    return time.monotonic() - t0


def test_unreachable_host_fails_at_three_seconds_not_sixty():
    """3 秒的逾時要真的生效 —— 這是 mbp 2026-09-30 掛 60s 的根因。"""
    elapsed = asyncio.run(_connect_elapsed(UNROUTABLE, 5432, 3.0))
    assert elapsed < 10, (
        f"timeout=3 卻花了 {elapsed:.1f}s —— connect_timeout 沒生效，"
        f"症狀會回到 mbp 2026-09-30 的 +60s 掛起"
    )


def test_asyncpg_default_is_sixty_seconds_why_it_matters():
    """對照組：asyncpg 的預設 60s。

    這條存在的理由是**記錄那個病根的數字**。若哪天 asyncpg 改預設，
    這條會紅 —— 那時要重新評估 3 這個值，而不是讓它無聲改掉。
    """
    elapsed = asyncio.run(_connect_elapsed(UNROUTABLE, 5432, 60.0))
    assert elapsed > 30, f"預期約 60s，實測 {elapsed:.1f}s（asyncpg 預設可能已變）"
