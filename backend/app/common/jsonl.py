"""JSONL 讀取：ingest 的 pg_load.py 與 qdrant_load.py 原本各有一份一模一樣的
`load_jsonl()`。語意相同（跳過空行、逐行 json.loads），收在此處。
"""
import json
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
