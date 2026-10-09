# B2-B — Judgment-to-Statute Provenance

Status: **implemented + gated — no answer generation, no baseline change.**
Code: `backend/app/b2b_cite.py` (new) + `b1_serve.py` span-extractor addition.
Tests: `tests/test_b2b_*.py` (4 files). Fixtures:
`tests/fixtures/b2b_citation_set.json` (11 reviewed cases),
`tests/fixtures/b2b_chain.json` (3 rank-1 chains).

## 1. Product objective

Make Judgment → Explicitly Cited Statute → Exact Statute Evidence mechanically
trustworthy: every returned statute traces to verbatim judgment text; anything
unresolvable is explicitly UNRESOLVED, never substituted, never dropped silently.

## 2. Architecture

`extract_citations(chunk)` → citation objects (EXPLICIT_CITATION only) →
exact corpus lookup → statute evidences (reused B1 objects + version flags) →
`statute_gate` S1–S8. No question input anywhere in the linker (structural
query-independence); no LLM; deterministic.

## 3. Citation contract (§5)

citation_id (`cit:{jid}#c{idx}:{start}-{end}`), judgement/document/chunk IDs,
raw_text, normalized{law,article,paragraph,subparagraph}, source_span
(code points, chunk basis), evidence_text (containing sentence, verbatim),
citation_type (constant), law_source (verbatim/xiachen_defined/unresolved),
scoped/scoped_from, resolution_status, evidence_id?, surface_collapsed.
Every field asserted in the frozen verified set.

## 4. Extraction rules (§8)

Qualified full-name mentions; same-sentence bare-article scoping (nearest
preceding article-level event, qualified or refused); paragraph-only ellipsis
anchored the same way; 下稱 parentheticals skipped between name and article;
refuse-list short forms (刑法/憲法 — grounded in 108 real corpus chunks);
non-statute shapes (鬮書, 卷第, 前項) never match. Single position-ordered
walk per sentence so ellipsis always sees its nearest anchor (two ordering
bugs found by fixture review and fixed before freezing).

## 5. Normalization rules (§6)

Corpus-exact law names; CJK/arabic numerals unified; 之-branch both with and
without trailing 條; spaced mentions via collapse with span-precise
surface_collapsed; hyphen/之 orthography resolved only to existing rows with
`orthography_mapped` flagged (436條之18→436-18 verified correct against the
judgment's own quotation).

## 6. Alias policy (§7)

No global alias map exists. ONLY document-internal `FULL（下稱SHORT）`
definitions authorize SHORT, and only when FULL is a corpus law
(demonstrated: 憲訴法→憲法訴訟法 resolves; 選罷法→選舉罷免法 unusable since
the FULL is not a corpus law). Everything else unknown → UNRESOLVED.

## 7. Resolution logic (§9)

Exact (law, article) lookup → row (pcode/article_seq/content). Article identity
is exact: 之-forms never fall back to base articles; paragraph ellipsis never
changes the article. APPLIED/DECISIVE labels are never emitted (constant +
tests); the strongest claim is "cited + resolved".

## 8. Verification set (§13)

11 cases / 35 citations, all real data (6 frozen corpus chunks incl. zero and
lookalike negatives; 5 B1-demo excerpts incl. wrapped/之-form/下稱 cases).
Built by generator (`agent/scripts/build_b2b_set.py`), then MANUALLY reviewed
against source text (two real bugs found and fixed during review). No synthetic
legal citations; synthetic strings only in negative parser tests.

## 9–11. Metrics (B2-B-EVIDENCE.json)

Verified set: extraction P/R/F1 1.0 (35/35 — regression value; discovery value
came from manual review), exact resolution 32/32, false resolutions 0,
provenance spans 35/35. Corpus profile (430 real chunks): 212 with citations,
672 resolved / 351 unresolved (350 unknown_alias — dominated by refused 刑法 —
1 no_corpus_match). Chain rollup (3 frozen rank-1 chains): gates all PASS
(GQ-001 3/0, GQ-002 14/2, GQ-005 24/0 resolved/unresolved).

## 12. Gate results (S1–S8)

PASS on the full verified set and all three chains. S4 (surface-in-text) and
S3 (exact pcode identity) are the load-bearing checks; S8 enforces corpus
version presence + historical-text-unverified flags (sync sha + 2026/9/24 date
recorded; per-article history unavailable — limitation, not engine).

## 13. B1 real-case result

Demo excerpts flow through the same path (V-07..V-11 frozen in the set);
B1 suite green, B1 module behavior unchanged (span-extractor is additive).

## 14. Regression results → validation report.

## 15. Limitations

L1 verified set is small (11 cases) — regression net, not coverage proof.
L2 corpus-wide profile is unlabeled (counts only). L3 no APPLIED/DECISIVE
relation (by design). L4 no per-article historical versions (corpus has
file-level sync record only). L5 第N項 bare-without-article outside sentences
with anchors stays unextracted.

## 16–17. Follow-ups → B2-B-FOLLOWUPS.md. Terminal: PASS → STOP (B2-C not started).
