#!/usr/bin/env python3
"""One-off B3-C reviewed-set builder (run by the batch author, then frozen).

Real disposition texts only: multi-judgment pairs (opposed + compatible),
single-documents (no-conflict), temporal-status inputs. Expected verdicts are
the author's verified judgments; tests assert exact equality afterwards.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "ingest/judgements"))

from app import b2c_evidence as E  # noqa: E402
from app import b3c_contra as K  # noqa: E402

recs = {r["chunk_id"]: r for r in
        json.loads(Path("/home/solo/artifacts/t007e05cb/out/corpus_snapshot.json")
                   .read_text(encoding="utf-8"))["records"]}


def disps_of(doc):
    out = []
    for r in recs.values():
        if r["document_id"] != doc:
            continue
        for d in E.extract_disposition(r):
            out.append({"evidence_id": d["evidence_id"], "judgement_id": doc,
                        "text": d["text"],
                        "outcome": K.normalize_disposition(d["text"])})
    return out


stev_node = None
_b2c = json.loads((REPO / "tests/fixtures/b2c_reasoning_set.json").read_text(encoding="utf-8"))
for _c in _b2c["cases"]:
    for _n in _c["graph"]["nodes"]:
        if _n["evidence_type"] == "DISPOSITION" and _n["judgement_id"] == "STEV,115,店小,450,20260713,1":
            stev_node = _n
assert stev_node is not None
from app import b3c_contra as _K
stev = [{"evidence_id": stev_node["evidence_id"],
         "judgement_id": "STEV,115,店小,450,20260713,1",
         "text": stev_node["text"],
         "outcome": _K.normalize_disposition(stev_node["text"])}]
jccc = disps_of("JCCC,115,審裁,1187,20260713")
chdm = disps_of("CHDM,115,聲,1164,20260716,1")
print("STEV:", [(d["outcome"], d["text"][:30]) for d in stev])
print("JCCC1187:", [(d["outcome"], d["text"][:30]) for d in jccc])
print("CHDM1164:", [(d["outcome"], d["text"][:30]) for d in chdm])

out = {"verification_status": "AUTHOR_REVIEWED",
       "provenance": "real frozen-corpus disposition texts via B2-C extraction",
       "cases": [
           {"case_id": "MC-01-opposed",
            "docs": ["STEV,115,店小,450,20260713,1", "CHDM,115,聲,1164,20260716,1"],
            "dispositions": [d for d in stev[:1] + chdm[:1]],
            "expected": "DIRECT"},
           {"case_id": "MC-02-compatible",
            "docs": ["STEV,115,店小,450,20260713,1", "JCCC,115,審裁,1187,20260713"],
            "dispositions": [d for d in stev[:1] + jccc[:1]],
            "expected": "no-DIRECT"},
           {"case_id": "NC-01-single",
            "docs": ["STEV,115,店小,450,20260713,1"],
            "dispositions": stev[:1],
            "expected": "no-DIRECT"},
       ]}
p = REPO / "tests/fixtures/b3c_contradiction_set.json"
p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"wrote {p} — MANUAL REVIEW REQUIRED")

# Hermetic multi-disposition slice: every corpus doc with >1 disposition chunk,
# vendored whole (texts + roles), for the zero-false-alarm test.
multi = []
_by_doc = {}
for _r in recs.values():
    _by_doc.setdefault(_r["document_id"], []).append(_r["chunk_id"])
def _vendored(_r):
    return {"chunk_id": _r["chunk_id"], "document_id": _r["document_id"], "text": _r["text"],
            "structural_role": _r["structural_role"],
            "structural_labels": _r.get("structural_labels", []),
            "carries_declared_disposition_unit": _r.get(
                "carries_declared_disposition_unit", False),
            "span": _r.get("span", {})}
for _doc, _cids in sorted(_by_doc.items()):
    _hit = [_vendored(recs[c]) for c in _cids if E.extract_disposition(recs[c])]
    if len(_hit) >= 2:
        multi.append({"document_id": _doc, "chunks": _hit})
_fx = json.loads((REPO / "tests/fixtures/b3c_contradiction_set.json").read_text(encoding="utf-8"))
_fx["multi_disposition_docs"] = multi
(REPO / "tests/fixtures/b3c_contradiction_set.json").write_text(
    json.dumps(_fx, ensure_ascii=False, indent=1), encoding="utf-8")
print("multi docs:", [(m["document_id"], len(m["chunks"])) for m in multi])
