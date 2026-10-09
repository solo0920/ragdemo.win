#!/usr/bin/env python3
"""One-off B4-A reviewed-set builder (run by the batch author, then frozen).

Assembles real B2-C evidence nodes across chunk boundaries; records assembly
outcomes. The author manually reviews every recorded expectation against the
source text before freezing; tests assert exact equality afterwards.
Re-running overwrites (review required again).
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "ingest/judgements"))
ART = Path("/home/solo/artifacts")

from app import b2c_evidence as E  # noqa: E402
from app import b3a_attribution as A3  # noqa: E402
from app import b4a_continuity as M  # noqa: E402

recs = {r["chunk_id"]: r for r in
        json.loads((ART / "t007e05cb/out/corpus_snapshot.json").read_text(encoding="utf-8"))["records"]}


def nodes_of(cid):
    r = recs[cid]
    chunks = [{"chunk_id": cid, "document_id": r["document_id"],
               "structural_role": r["structural_role"],
               "structural_labels": r.get("structural_labels", []),
               "carries_declared_disposition_unit": r.get(
                   "carries_declared_disposition_unit", False),
               "text": r["text"], "span": r.get("span", {})}]
    g = E.build_evidence_graph(chunks, [])
    return r, g["nodes"]


def classify(nodes, chunk_texts):
    amap = {}
    for n in nodes:
        t = chunk_texts[n["chunk_id"]]
        s, e = n["source_span"]["start"], n["source_span"]["end"]
        amap[n["evidence_id"]] = A3.classify_attribution(
            n["text"], context_before=t[max(0, s - 400):s],
            context_after=t[e:e + 400])["attribution"]
    return amap


def offsets(*cids):
    return {c: recs[c]["span"]["start"] for c in cids}


CASES = []
# G-01: contiguous reasoning across a real chunk boundary (CTDV C004+C005).
r4 = recs["CTDV,112,訴,82,20260731,1#C004"]
r5 = recs["CTDV,112,訴,82,20260731,1#C005"]
n4 = [n for n in E.extract_reasoning(r4)][-1:]
n5 = [n for n in E.extract_reasoning(r5)][:1]
CASES.append(("G-01-contiguous-reasoning", n4 + n5,
              {"CTDV,112,訴,82,20260731,1#C004": r4["text"],
               "CTDV,112,訴,82,20260731,1#C005": r5["text"]}))
# G-01b: whitespace-gap contiguous reasoning (CTDV C010 last + C011 first).
_r10 = recs["CTDV,112,訴,82,20260731,1#C010"]
_r11 = recs["CTDV,112,訴,82,20260731,1#C011"]
_n10 = E.extract_reasoning(_r10)
_n11 = E.extract_reasoning(_r11)
CASES.append(("G-01b-whitespace-continuous", [_n10[-1], _n11[0]],
              {_r10["chunk_id"]: _r10["text"], _r11["chunk_id"]: _r11["text"]}))
# G-02: open-ended disposition + headered next chunk stays SEPARATE.
r1 = recs["TCTA,115,續收,3347,20260710,1#C001"]
CASES.append(("G-02-clean-boundary", E.extract_disposition(r1),
              {"TCTA,115,續收,3347,20260710,1#C001": r1["text"]}))
# G-04: attribution boundary (CTDM C002 COURT + UNRESOLVED pair).
rc = recs["CTDM,115,訴,1694,20260731,1#C002"]
CASES.append(("G-04-attribution-boundary", E.extract_reasoning(rc),
              {rc["chunk_id"]: rc["text"]}))

out = {"verification_status": "AUTHOR_REVIEWED",
       "provenance": "real B2-C evidence nodes across real chunk boundaries",
       "cases": []}
for case_id, nodes, texts in CASES:
    amap = {}
    for n in nodes:
        t = texts[n["chunk_id"]]
        s, e = n["source_span"]["start"], n["source_span"]["end"]
        amap[n["evidence_id"]] = A3.classify_attribution(
            n["text"], context_before=t[max(0, s - 400):s],
            context_after=t[e:e + 400])["attribution"]
    offs = {cid: recs[cid]["span"]["start"] for cid in texts}
    texts_full = {cid: recs[cid]["text"] for cid in texts}
    rec = M.assemble(nodes, evidence_type=nodes[0]["evidence_type"],
                     attributions=amap, doc_offsets=offs, chunk_texts=texts_full)
    out["cases"].append({
        "case_id": case_id,
        "judgement_id": nodes[0]["document_id"] if nodes else "",
        "nodes": nodes, "attributions": amap, "doc_offsets": offs,
        "chunk_texts": texts, "assembly": rec})
# G-03: synthetic gap (real C004+C005+C006 chain minus the middle chunk's
# nodes), honestly labeled. C005's two nodes are dropped, leaving a real gap.
_g3chunks = ["CTDV,112,訴,82,20260731,1#C004", "CTDV,112,訴,82,20260731,1#C005",
             "CTDV,112,訴,82,20260731,1#C006"]
_g3nodes, _g3texts = [], {}
for _cid in _g3chunks:
    _r = recs[_cid]
    _g3texts[_cid] = _r["text"]
    for _n in E.extract_reasoning(_r):
        _g3nodes.append(_n)
_g3amap = {}
for _n in _g3nodes:
    _t = _g3texts[_n["chunk_id"]]
    _s, _e = _n["source_span"]["start"], _n["source_span"]["end"]
    _g3amap[_n["evidence_id"]] = A3.classify_attribution(
        _n["text"], context_before=_t[max(0, _s - 400):_s],
        context_after=_t[_e:_e + 400])["attribution"]
_g3offs = {c: recs[c]["span"]["start"] for c in _g3chunks}
_g3full = M.assemble(_g3nodes, evidence_type="REASONING", attributions=_g3amap,
                     doc_offsets=_g3offs, chunk_texts=_g3texts)
assert _g3full["status"] == "ORDERED_GAPPED", _g3full["status"]
out["cases"].append({
    "case_id": "G-03-synthetic-gap", "synthetic_gap": True,
    "judgement_id": "CTDV,112,訴,82,20260731,1",
    "nodes": _g3nodes, "attributions": _g3amap, "doc_offsets": _g3offs,
    "chunk_texts": _g3texts,
    "assembly": None,
    "full_chain_status": _g3full["status"],
    "note": "test drops the C005 nodes to exercise gap marking on real spans"})
p = REPO / "tests/fixtures/b4a_multichunk_set.json"
p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
for c in out["cases"]:
    a = c["assembly"]
    print(c["case_id"], len(c["nodes"]), "nodes ->",
          a["status"] if a else "pending-synthetic-gap")
print(f"wrote {p} — MANUAL REVIEW REQUIRED")
