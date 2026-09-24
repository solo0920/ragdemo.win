import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 讓測試可匯入 backend/app 與 ingest/laws 的純函式模組
for sub in ("backend", "ingest/laws"):
    p = ROOT / sub
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))