"""S2a P0 link-validator tests: mechanism only, never gold labels.

All inputs are synthetic (JX-prefixed, never real law). They prove the
validator's fail-closed mechanics: recomputed continuity, per-fragment
voice/conclusory checks, coordinate-space binding. They do NOT establish
that any cross-chunk case is a real court application (that needs reviewed
real positives, blocked P2).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app import b3e_applied as E3  # noqa: E402
from app import b4a_continuity as C4  # noqa: E402

T1 = "原告依民法第184條之規定請求，"
T2 = "為無理由，應予駁回。"
FULL = T1 + T2
CIT_START = T1.find("民法第184條")
CIT_END = CIT_START + len("民法第184條")


def _cit():
    # evidence_text is the chunk-local containing sentence (B2-B emits one
    # per chunk); here the citation lives in the first chunk's sentence.
    return {"citation_id": "cit:JX#C1:4-11", "judgement_id": "JX",
            "chunk_id": "JX#chunk0", "raw_text": "民法第184條",
            "source_span": {"start": CIT_START, "end": CIT_END,
                            "unit": "code_point", "basis": "chunk_text"},
            "evidence_text": T1}


def _frag(evid, chunk, text, start, off):
    return {"evidence_id": evid, "evidence_type": "HOLDING",
            "judgement_id": "JX", "chunk_id": chunk, "text": text,
            "source_span": {"start": start, "end": start + len(text),
                            "unit": "code_point", "basis": "chunk_text"},
            "provenance": {"method": "conclusory_pattern",
                           "marker": "為無理由", "heuristic": True},
            "_off": off}


def _world():
    f1 = _frag("holding:JX#C1:0-13", "JX#C1", T1, 0, 0)
    f2 = _frag("holding:JX#C2:0-11", "JX#C2", T2, 0, len(T1))
    amap = {f1["evidence_id"]: "COURT_VOICE",
            f2["evidence_id"]: "COURT_VOICE"}
    texts = {"JX#C1": T1, "JX#C2": T2}
    offs = {"JX#C1": 0, "JX#C2": len(T1)}
    return f1, f2, amap, texts, offs


def _strip(f):
    f = dict(f)
    f.pop("_off", None)
    return f


def _valid_kwargs():
    f1, f2, amap, texts, offs = _world()
    cit_text = T1  # citation extracted from the first chunk's text
    return {"citation": _cit(), "citation_text": cit_text,
            "fragments": [_strip(f1), _strip(f2)], "attribution_map": amap,
            "doc_offsets": offs, "chunk_texts": texts}


def test_p0_valid_link_ok_without_promotion():
    # Mechanism passes, but APPLIED is never promoted (S2a has no promotion).
    out = E3.validate_link(**_valid_kwargs())
    assert out["verdict"] == E3.LINK_OK, out
    assert out["continuity_status"] == C4.CONTIGUOUS
    assert out["assembly_id"].startswith("applied-multi:")
    assert len(out["covering_rule_ids"]) == 2
    # Same inputs through the frozen classifier stay UNRESOLVED.
    rel = E3.link_application(_cit(), {}, {}, {})
    assert rel["relation_type"] == E3.APPLICATION_UNRESOLVED


def test_p0_missing_offsets_fail():
    kw = _valid_kwargs()
    kw["doc_offsets"] = {}
    out = E3.validate_link(**kw)
    assert (out["verdict"], out["reason"]) == (E3.LINK_FAIL, "continuity-not-contiguous:CONTINUITY_UNKNOWN")


def test_p0_gapped_unknown_overlap_fail():
    kw = _valid_kwargs()
    # Gap: move second chunk 50 chars forward (non-whitespace gap unknown).
    kw["doc_offsets"] = {"JX#C1": 0, "JX#C2": len(T1) + 50}
    out = E3.validate_link(**kw)
    assert out["verdict"] == E3.LINK_FAIL
    assert out["reason"].startswith("continuity-not-contiguous:")
    # Overlap: move second chunk backwards into the first.
    kw["doc_offsets"] = {"JX#C1": 0, "JX#C2": 5}
    out = E3.validate_link(**kw)
    assert out["verdict"] == E3.LINK_FAIL


def test_p0_cross_judgement_fail():
    kw = _valid_kwargs()
    frags = [dict(f) for f in kw["fragments"]]
    frags[1]["judgement_id"] = "JY"
    kw["fragments"] = frags
    out = E3.validate_link(**kw)
    assert (out["verdict"], out["reason"]) == (E3.LINK_FAIL, "cross-judgement-fragments")


def test_p0_fragment_voice_and_method_fail():
    kw = _valid_kwargs()
    kw["attribution_map"] = {"holding:JX#C1:0-13": "COURT_VOICE",
                             "holding:JX#C2:0-11": "PARTY_VOICE"}
    assert E3.validate_link(**kw)["reason"] == "fragment-attribution-not-court"
    kw = _valid_kwargs()
    frags = [dict(f, provenance={"method": "court_voice_marker"}) if i == 1 else f
             for i, f in enumerate(kw["fragments"])]
    kw["fragments"] = frags
    assert E3.validate_link(**kw)["reason"] == "fragment-not-conclusory"
    kw = _valid_kwargs()
    frags = [dict(f, evidence_type="REASONING") if i == 0 else f
             for i, f in enumerate(kw["fragments"])]
    kw["fragments"] = frags
    assert E3.validate_link(**kw)["reason"] == "fragment-not-holding"
    # Missing map entries fail closed (None map).
    kw = _valid_kwargs()
    kw["attribution_map"] = None
    assert E3.validate_link(**kw)["reason"] == "fragment-attribution-not-court"


def test_p0_citation_mismatch_and_unpaired_fail():
    kw = _valid_kwargs()
    kw["citation_text"] = "無關文字。"
    assert E3.validate_link(**kw)["reason"] == "citation-surface-mismatch"
    # A-09 shape: citation text and fragments from different sources.
    kw = _valid_kwargs()
    kw["citation_text"] = T2
    kw["chunk_texts"] = {"JX#C1": T1, "JX#C2": T2}
    out = E3.validate_link(**kw)
    assert out["verdict"] == E3.LINK_FAIL
    assert out["reason"] in ("citation-surface-mismatch",
                             "citation-not-in-exactly-one-fragment",
                             "citation-space-mismatch")


def test_p0_forged_claims_ignored():
    # Caller-supplied status/rule IDs are recorded, never pass evidence.
    kw = _valid_kwargs()
    kw["claimed"] = {"continuity_status": "CONTIGUOUS",
                     "covering_rule_ids": ["anything-goes"]}
    out = E3.validate_link(**kw)
    assert out["verdict"] == E3.LINK_OK
    assert out["claimed"] == kw["claimed"]
    assert "anything-goes" not in out["covering_rule_ids"]
    # And they cannot rescue invalid inputs.
    kw["doc_offsets"] = {}
    out = E3.validate_link(**kw)
    assert out["verdict"] == E3.LINK_FAIL


def test_p0_malformed_never_raises():
    assert E3.validate_link(citation={}, citation_text="", fragments=[],
                            attribution_map=None, doc_offsets={},
                            chunk_texts={})["verdict"] == E3.LINK_FAIL
    assert E3.validate_link(citation=None, citation_text=None, fragments=None,
                            attribution_map=None, doc_offsets=None,
                            chunk_texts=None)["verdict"] == E3.LINK_FAIL
    kw = _valid_kwargs()
    kw["fragments"] = [{"evidence_id": "x"}]
    out = E3.validate_link(**kw)
    assert out["verdict"] == E3.LINK_FAIL


def test_p0_assembly_id_deterministic_and_bound():
    kw = _valid_kwargs()
    a = E3.validate_link(**kw)["assembly_id"]
    b = E3.validate_link(**kw)["assembly_id"]
    assert a == b
    assert a == E3.multi_assembly_id(
        "cit:JX#C1:4-11", "JX", ["JX#C1", "JX#C2"],
        [{"start": 0, "end": len(T1)}, {"start": 0, "end": len(T2)}])
    kw["fragments"] = [dict(f) for f in kw["fragments"]]
    kw["fragments"][1]["chunk_id"] = "JX#C9"
    c = E3.validate_link(**kw)
    assert c["verdict"] == E3.LINK_FAIL  # chunk/text/offset binding broken


def test_p0_wrong_chunk_same_text_same_offsets_rejected():
    # Mechanism test (synthetic, not legal gold): the citation sentence
    # occurs in another chunk at the SAME numeric offsets, but that chunk's
    # surrounding text differs. Span coverage and sentence containment both
    # pass numerically, so only the coordinate-space binding
    # (citation-space-mismatch) refuses the bridge. Removing that check
    # would let this reach LINK_OK (continuity itself is CONTIGUOUS here).
    sent = "原告依民法第184條請求。"
    cs = sent.find("民法第184條")
    ce = cs + len("民法第184條")
    home_text = "前言。" + sent + "後語。"          # citation's own chunk text
    other_text = "他說：" + sent + "結束。"         # same sentence, same span
    assert other_text[0:16] != home_text[0:16]  # surroundings genuinely differ
    frag = {"evidence_id": "holding:JX#C9:0-16", "evidence_type": "HOLDING",
            "judgement_id": "JX", "chunk_id": "JX#C9",
            "text": other_text[0:16],
            "source_span": {"start": 0, "end": 16, "unit": "code_point",
                            "basis": "chunk_text"},
            "provenance": {"method": "conclusory_pattern",
                           "marker": "為無理由", "heuristic": True}}
    cit = {"citation_id": "cit:JX#C1:6-12", "judgement_id": "JX",
           "chunk_id": "JX#chunk0", "raw_text": "民法第184條",
           "source_span": {"start": 3 + cs, "end": 3 + ce,
                           "unit": "code_point", "basis": "chunk_text"},
           "evidence_text": sent}
    amap = {frag["evidence_id"]: "COURT_VOICE"}
    texts = {"JX#C9": other_text}
    offs = {"JX#C9": 100}
    out = E3.validate_link(citation=cit, citation_text=home_text,
                           fragments=[frag], attribution_map=amap,
                           doc_offsets=offs, chunk_texts=texts)
    assert (out["verdict"], out["reason"]) == (
        E3.LINK_FAIL, "citation-space-mismatch")
    # Isolation proof: every other check passes on these inputs — continuity
    # recomputed alone is CONTIGUOUS, so the space binding is the guard.
    rec = C4.assemble(
        [{"evidence_id": frag["evidence_id"], "evidence_type": "HOLDING",
          "judgement_id": "JX", "chunk_id": "JX#C9",
          "text": frag["text"], "source_span": dict(frag["source_span"])}],
        evidence_type="HOLDING",
        attributions={frag["evidence_id"]: "COURT_VOICE"},
        doc_offsets=dict(offs), chunk_texts=dict(texts))
    assert rec.get("status") == C4.CONTIGUOUS
    # And the correctly bound counterpart (same sentence from its own chunk)
    # is still accepted: the test discriminates binding, not content.
    home_frag = dict(frag, evidence_id="holding:JX#C1:0-16", chunk_id="JX#C1",
                       text=home_text[0:16],
                       source_span={"start": 0, "end": 16})
    home_texts = {"JX#C1": home_text}
    home_offs = {"JX#C1": 100}
    home_amap = {home_frag["evidence_id"]: "COURT_VOICE"}
    ok = E3.validate_link(citation=cit, citation_text=home_text,
                          fragments=[home_frag], attribution_map=home_amap,
                          doc_offsets=home_offs, chunk_texts=home_texts)
    assert ok["verdict"] == E3.LINK_OK, ok
