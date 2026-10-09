"""從 repo 內既有的 8-key golden 文件造出 `EntryResult`，供測試餵給 manifest。

**為什麼需要這個輔助模組**：`build_manifest` 需要的是「已經走完管線的結果」，
而測試不該為了造出那個狀態去解壓 284MB 的 archive（原則 V：測試要快、要
可重現）。所以用 `tests/fixtures/judgements/golden/docs_*.json` 走一次
`_run_stages` —— 那是真的管線，只是不是真的 archive。

⚠ 這不是 mock：`profile_build._run_stages` 是**真的**被呼叫，schema 驗證與
chunking 都真的跑了。唯一的差別是 bytes 從磁碟讀來而不是從 RAR 解壓來。
"""
from __future__ import annotations

import hashlib
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "ingest" / "judgements") not in sys.path:
    sys.path.insert(0, str(ROOT / "ingest" / "judgements"))

import profile_build as PB  # noqa: E402

GOLDEN_DIR = ROOT / "tests" / "fixtures" / "judgements" / "golden"


def stage_results():
    """回傳一批真的跑過管線的 `EntryResult`（全部成功）。"""
    out = []
    for p in sorted(GOLDEN_DIR.glob("docs_*.json")):
        data = p.read_bytes()
        rel = f"202607\\臺灣臺北地方法院民事\\{p.name}"
        entry = PB.Entry(
            path=rel,
            path_posix=rel.replace("\\", "/"),
            unpacked_size=len(data),
            crc32="%08X" % zlib.crc32(data),
        )
        res = PB.EntryResult(
            entry=entry,
            extracted=True,
            size_crc_ok=True,
            sha256=hashlib.sha256(data).hexdigest(),
            crc32_actual=entry.crc32,
        )
        out.append(PB._run_stages(res, data))
    return out
