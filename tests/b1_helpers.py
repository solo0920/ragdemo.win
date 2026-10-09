"""B1 helper: real T015/T017-built payload from the real demo fixture."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for sub in ("backend", "ingest/judgements"):
    p = ROOT / sub
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import chunk as CH  # noqa: E402
import index_load as IL  # noqa: E402
import text as T  # noqa: E402
from app import b1_serve as B1  # noqa: E402

FIXTURE = json.loads(
    (ROOT / "tests" / "fixtures" / "b1_judgement_serving" / "case_00450.json")
    .read_text(encoding="utf-8")
)


def load_case():
    return FIXTURE


def real_chunks(entry_path=None, jid=None, jfull=None, chunk_size=600):
    """Real chunk_text() output on the real demo JFULL."""
    fx = FIXTURE
    doc = SimpleNamespace(
        entry_path=entry_path or fx["entry_path"],
        jid=jid or fx["document"]["JID"],
        jyear=fx["document"]["JYEAR"],
        jdate=fx["document"]["JDATE"],
        jcase=fx["document"]["JCASE"],
    )
    chunks = CH.chunk_text(
        T.LosslessText.from_string(jfull if jfull is not None else fx["document"]["JFULL"]),
        chunk_size=chunk_size,
        source_document=doc.entry_path,
        jid=doc.jid,
    )
    return doc, chunks


def real_hits(doc=None, chunks=None, scores=(0.9, 0.8, 0.7)):
    """Real index_load-built payloads shaped as search_judgments hits."""
    if chunks is None:
        doc, chunks = real_chunks()
    hits = []
    for i, c in enumerate(chunks):
        pt = IL.build_point(c, doc)
        hits.append({
            "id": pt["id"],
            "payload": pt["payload"],
            "score": scores[i % len(scores)],
        })
    return hits


def real_corpus():
    return B1.StatuteCorpus.from_jsonl()


def jfull_map():
    fx = FIXTURE
    return {fx["entry_path"]: fx["document"]["JFULL"]}
