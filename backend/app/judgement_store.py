"""Live judgement document store (S1): JFULL + metadata from frozen seed files.

Seed files live in data/judgements/seed/*.json, written byte-reproducibly by
agent/scripts/seed_judgements_index.py from reviewed fixtures + frozen
chunk.py. Each file carries entry_path / jid / jyear / jdate / jcase / jfull /
chunks — all real frozen values, no inference.

Serving paths use jfull_map() as the default jfull_by_entry: absent entries
(and load failures) yield {} so callers keep their honest-abstain behavior.
Nothing here fabricates text: unknown entry_path -> None -> caller abstains.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger("ragdemo")

_SEED_DIRS = (Path("data/judgements/seed"), Path("/app/data/judgements/seed"))
_CACHE: tuple[float, dict] = (0.0, {})
_TTL = 60.0


def _dirs() -> list[Path]:
    return [d for d in _SEED_DIRS if d.is_dir()]


def _load() -> dict:
    """{entry_path: seed_doc}. Best-effort: unreadable files are skipped."""
    out: dict = {}
    for d in _dirs():
        for p in sorted(d.glob("*.json")):
            try:
                doc = json.loads(p.read_text(encoding="utf-8"))
            except Exception as e:  # bad seed file must not break serving
                logger.warning("judgement seed 讀取失敗 %s: %s", p, e)
                continue
            if isinstance(doc, dict) and doc.get("entry_path") and isinstance(
                    doc.get("jfull"), str):
                out[doc["entry_path"]] = doc
    return out


def _store() -> dict:
    global _CACHE
    ts, val = _CACHE
    if time.time() - ts < _TTL:
        return val
    val = _load()
    _CACHE = (time.time(), val)
    return val


def jfull_map() -> dict[str, str]:
    """entry_path -> JFULL for every seeded document ({} when none)."""
    try:
        return {k: v["jfull"] for k, v in _store().items()}
    except Exception as e:  # noqa: BLE001 - store must fail closed
        logger.warning("judgement store 不可用: %s", e)
        return {}


def doc_meta(entry_path: str) -> dict | None:
    """Metadata for one seeded document (None when unknown)."""
    d = _store().get(entry_path)
    if not d:
        return None
    return {k: d.get(k) for k in ("entry_path", "jid", "jyear", "jdate", "jcase")}
