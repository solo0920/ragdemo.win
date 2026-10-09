"""B2-C structure tests: frozen verified-set equality + heading variants."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for sub in ("backend", "ingest/judgements"):
    p = ROOT / sub
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from app import b2c_evidence as E  # noqa: E402
from app import b2b_cite as C  # noqa: E402
from app import b1_serve as B1  # noqa: E402

SET = json.loads((ROOT / "tests/fixtures/b2c_reasoning_set.json").read_text(encoding="utf-8"))
CORPUS = B1.StatuteCorpus.from_jsonl()


def _graph(case):
    cites = []
    for ch in case["chunks"]:
        cites.extend(C.extract_citations(
            ch["text"], jid=ch["document_id"], chunk_index=ch["chunk_index"],
            corpus=CORPUS))
    return E.build_evidence_graph(case["chunks"], cites)


def test_frozen_verified_set_matches_exactly():
    for case in SET["cases"]:
        g = _graph(case)
        assert g["nodes"] == case["graph"]["nodes"], case["case_id"]
        assert g["edges"] == case["graph"]["edges"], case["case_id"]
        ok, failures = E.reasoning_gate(
            g, {ch["chunk_id"]: ch["text"] for ch in case["chunks"]})
        assert (ok, failures) == ((case["gate"]["verdict"] == "PASS"),
                                  case["gate"]["failures"])


def _chunk(text, role="FACTS", labels=("FACTS",), jid="J", cid="J#C1"):
    return {"chunk_id": cid, "document_id": jid, "structural_role": role,
            "structural_labels": list(labels), "text": text, "span": {}}


def test_disposition_heading_variants():
    for heading in ["主文", "主　文", "主  文", "主\u3000\u3000\u3000文", "裁判主文"]:
        ds = E.extract_disposition(_chunk(f"\r\n　　{heading}\r\n原告之訴駁回。"))
        assert len(ds) == 1 and ds[0]["evidence_type"] == "DISPOSITION", heading
        assert ds[0]["text"].endswith("原告之訴駁回。")


def test_disposition_references_rejected():
    for text in ["裁定如主文。", "量處如主文所示之刑。", "逕以簡易判決處刑如主文。"]:
        assert E.extract_disposition(_chunk(text)) == [], text


def test_marker_absent_flagged_chunk():
    ds = E.extract_disposition(_chunk("臺灣新北地方法院支付命令", role="HEADER",
                                      labels=("DISPOSITION_PROXY", "HEADER"),
                                      jid="P", cid="P#C1"))
    assert ds == []  # no label flag without the unit marker
    ch = _chunk("臺灣新北地方法院支付命令", role="HEADER",
                labels=("DISPOSITION_PROXY", "HEADER"), jid="P", cid="P#C1")
    ch["carries_declared_disposition_unit"] = True
    ds = E.extract_disposition(ch)
    assert len(ds) == 1 and ds[0]["provenance"]["method"] == "marker_absent_flagged_chunk"


def test_holding_needs_conclusory_pattern():
    assert E.extract_holding(_chunk("原告並未受有實際損害，難認有據。"), []) != []
    assert E.extract_holding(_chunk("原告住臺北市。"), []) == []


def test_disposition_territory_never_holding():
    ch = _chunk("主文\r\n原告之訴駁回，為無理由。")
    ds = E.extract_disposition(ch)
    spans = [(d["source_span"]["start"], d["source_span"]["end"]) for d in ds]
    assert E.extract_holding(ch, spans) == []
