"""B4-A unit: ordering, continuity states, attribution/type guards."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app import b4a_continuity as M  # noqa: E402

OFF = {"C1": 0, "C2": 100}
ATT = {"e1": "COURT_VOICE", "e2": "COURT_VOICE"}


def frag(eid, cid, start, end, text=None, jid="J", etype="REASONING"):
    return {"evidence_id": eid, "chunk_id": cid, "judgement_id": jid,
            "text": text if text is not None else f"<{eid}>",
            "source_span": {"start": start, "end": end, "unit": "code_point",
                            "basis": "chunk_text"},
            "evidence_type": etype}


def test_contiguous_assembly_preserves_order_and_text():
    off = {"C1": 0, "C2": 10}
    r = M.assemble([frag("e2", "C2", 0, 10, "bbbb"),
                    frag("e1", "C1", 0, 10, "aaaa")],
                   evidence_type="REASONING", attributions=ATT, doc_offsets=off)
    assert r["status"] == "CONTIGUOUS"
    assert r["text"] == "aaaabbbb"  # source order, not input order
    assert r["chunk_ids"] == ["C1", "C2"]
    assert r["gaps"] == []


def test_reversed_input_sorted_not_rejected():
    r = M.assemble([frag("e1", "C1", 0, 10, "aaaa")],
                   evidence_type="REASONING", attributions=ATT, doc_offsets=OFF)
    assert r["status"] == "CONTIGUOUS" and r["text"] == "aaaa"


def test_gapped_assembly_marks_gaps():
    r = M.assemble([frag("e1", "C1", 0, 10, "aaaa"),
                    frag("e3", "C3", 0, 10, "cccc")],
                   evidence_type="REASONING",
                   attributions={"e1": "COURT_VOICE", "e3": "COURT_VOICE"},
                   doc_offsets={"C1": 0, "C3": 200})
    assert r["status"] == "ORDERED_GAPPED"
    assert M.GAP_MARKER in r["text"]
    assert r["gaps"][0]["span_start"] == 10 and r["gaps"][0]["span_end"] == 200
    assert r["fragments"] == ["aaaa", "cccc"]


def test_different_documents_never_join():
    r = M.assemble([frag("e1", "C1", 0, 10), frag("e2", "C2", 0, 10, jid="K")],
                   evidence_type="REASONING", attributions=ATT, doc_offsets=OFF)
    assert r["status"] == "SEPARATE_EVIDENCE"


def test_missing_spans_or_offsets_is_unknown():
    r = M.assemble([{"evidence_id": "e1", "chunk_id": "C1", "judgement_id": "J",
                     "text": "x", "evidence_type": "REASONING"}],
                   evidence_type="REASONING", attributions=ATT, doc_offsets=OFF)
    assert r["status"] == "CONTINUITY_UNKNOWN"
    r = M.assemble([frag("e1", "C1", 0, 10)], evidence_type="REASONING",
                   attributions=ATT, doc_offsets={})
    assert r["status"] == "CONTINUITY_UNKNOWN"


def test_attribution_boundary_splits():
    r = M.assemble([frag("e1", "C1", 0, 10), frag("e2", "C2", 0, 10)],
                   evidence_type="REASONING",
                   attributions={"e1": "COURT_VOICE", "e2": "PARTY_VOICE"},
                   doc_offsets=OFF)
    assert r["status"] == "SEPARATE_EVIDENCE"
    assert "PARTY_VOICE" in r["reason"]
    # Missing map entries fail closed too.
    r = M.assemble([frag("e1", "C1", 0, 10)], evidence_type="REASONING",
                   attributions={}, doc_offsets=OFF)
    assert r["status"] == "SEPARATE_EVIDENCE"


def test_mixed_types_split_and_duplicates_rejected():
    r = M.assemble([frag("e1", "C1", 0, 10, etype="REASONING"),
                    frag("e2", "C2", 0, 10, etype="HOLDING")],
                   evidence_type="REASONING", attributions=ATT, doc_offsets=OFF)
    assert r["status"] == "SEPARATE_EVIDENCE"
    r = M.assemble([frag("e1", "C1", 0, 10), frag("e1", "C1", 0, 10)],
                   evidence_type="REASONING", attributions=ATT, doc_offsets=OFF)
    assert r["status"] in ("CONTIGUOUS", "CONTINUITY_UNKNOWN", "SEPARATE_EVIDENCE")
    # identical spans overlap -> UNKNOWN (never silently merged)
    assert r["status"] == "CONTINUITY_UNKNOWN"


def test_remap_citation_span():
    spans = [{"chunk_id": "C1", "start": 0, "end": 10},
             {"chunk_id": "C2", "start": 0, "end": 10}]
    assert M.remap_citation_span(5, spans) == {"chunk_id": "C1", "local_offset": 5}
    assert M.remap_citation_span(12, spans) == {"chunk_id": "C2", "local_offset": 2}
    assert M.remap_citation_span(99, spans) is None
