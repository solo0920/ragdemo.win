# B4-A — Multi-Chunk Evidence Continuity

Status: **implemented + enforced on the live path — deterministic, no LLM.**
Code: `backend/app/b4a_continuity.py` (new) + narrow B2-D wiring
(`_enforce_continuity`, `enforce_continuity` flag, `serve_live` enforcement).
Tests: `tests/test_b4a_{assemble,gate,chain,serve}.py` (25).
Fixture: `tests/fixtures/b4a_multichunk_set.json` (5 reviewed cases).

## 1. Motivation

A legally meaningful passage may cross retrieval chunk boundaries. Quoting a
single fragment as if it were the complete court reasoning fabricates
completeness. B4-A preserves complete, ordered, source-faithful evidence when
—and only when— continuity is mechanically provable.

## 2. Current B2-C limitation (unchanged behavior)

B2-C extracts per-chunk evidence with `incomplete`/`continues_in_next_chunk`
flags but never joins chunks. B4-A consumes those flags; B2-C semantics are
untouched (B2-C suite green).

## 3. Chunk/source model

Corpus chunks tile the source losslessly with document-absolute spans
(`content.raw_text`, code points; corpus-wide census: 356 contiguous vs 0
gapped adjacent chunk pairs — chunks tile, evidence nodes usually do not).
Evidence nodes carry chunk-relative spans; absolute position = chunk offset +
node offset. Consecutive chunk IDs prove nothing; only spans order.

## 4. Continuity semantics

CONTIGUOUS (proven adjacency incl. preserved whitespace bridges) /
ORDERED_GAPPED (ordered, gaps explicit via `GAP_MARKER`) / SEPARATE_EVIDENCE
(wrong document/type/attribution — kept apart) / CONTINUITY_UNKNOWN (missing
spans/offsets/overlap — never joined). Only CONTIGUOUS renders as one
verbatim passage.

## 5. Attribution interaction (B3-A authoritative)

Per-node B3-A statuses; any non-COURT_VOICE fragment splits (never merges);
missing map entries fail closed as UNRESOLVED. Verified: G-04 stays SEPARATE
on a real COURT/UNRESOLVED boundary.

## 6. Evidence graph

`_enforce_continuity` completes span-verified cut dispositions, assembles
same-type groups via `cont.assemble`, drops uncompletable incomplete
dispositions, replaces proven groups with multi nodes carrying
`chunk_ids`/`source_spans`/`assembly_id` provenance. Material
(disposition/holding) UNKNOWN → caller abstains
INSUFFICIENT_CONTINUITY_EVIDENCE. Contradiction/attribution/temporal gates
run on the assembled graph unchanged.

## 7. Reviewed real-data set

G-01 ORDERED_GAPPED (real 834-char evidence gap, marked) · G-01b CONTIGUOUS
(whitespace bridge preserved byte-exact) · G-02 single-chunk CONTIGUOUS
(clean boundary, no false join) · G-04 SEPARATE (attribution boundary) ·
G-03 synthetic gap (labeled; middle chunk dropped → ORDERED_GAPPED).
All recompute byte-identical (B4-A-EVIDENCE.json).

## 8–10. Continuity / false-concatenation / attribution-contamination metrics

Reviewed: 5/5 match, 0 false concatenations, 0 attribution contaminations.
Corpus probe: reversed input sorts to source order (never flipped).
Gate M1–M10 unit-pinned including M10 provenance-stripping rejection.

## 11. Truncation analysis

Incomplete dispositions that verify complete in full; unverifiable ones drop,
and the answer layer abstains downstream when mandatory evidence is missing.
No silent truncation observed in reviewed cases.

## 12. B2-D integration

`serve_question(enforce_continuity=True)` gates before composition; gate
rejection abstains and `build_answer` never runs (call-count pinned).
`serve_live` enforces all gates including continuity; the `/judgments/answer`
route forwards `require_statutes` explicitly (previously dropped → 500).

## 13–14. B3-A / B3-C interaction

Attribution computed per node via `attribute_graph_nodes`; contradiction
checks run on assembled nodes with full provenance (ids/spans preserved).

## 15–17. Regression / limitations / terminal

- Full suite 1624 passed, 0 failed; 3 validators PASS; 8/8 fingerprints match.
- L1 split citations across chunks are not reassembled (limitation, no
  fabrication). L2 multi node `chunk_id` is the first chunk (full list in
  provenance). L3 500-char disposition-completion cap is arbitrary but bounded
  and source-faithful.
- Terminal: PASS → STOP (B4-B not started).
