# Tasks: Judicial Source Fidelity

**Input**: Design documents from `/specs/004-judicial-source-fidelity/`
**Prerequisites**: `plan.md`, `spec.md`, and the three evidence documents:
[`decoder-proof.md`](./decoder-proof.md) · [`corpus-schema-survey.md`](./corpus-schema-survey.md) · [`citation-pattern-survey.md`](./citation-pattern-survey.md)

**Scope**: task definition only. No implementation has begun.

## Layer labels

| Label | Meaning |
|---|---|
| **[A]** | authoritative — source-of-truth data; a task touching this may only **read** |
| **[D]** | derived — computed from authoritative data; must be reproducible |
| **[I]** | inferred — explicitly provisional; MUST NOT enter a contract |
| **[O]** | optional / future — out of scope for this iteration |

## Invariants referenced by tasks

| Key | Invariant |
|---|---|
| **INV-SRC** | `JFULL` remains byte-exact source text |
| **INV-SUB** | `text == JFULL[start:end]` exactly |
| **INV-PROV** | derived object resolves to artifact hash → entry → field → offset |
| **INV-NOFAB** | no statute name, article meaning, summary, or conclusion is generated |
| **INV-REPRO** | same artifact + decoder + algorithm ⇒ identical output |
| **INV-SEP** | authoritative text and derived metadata never merged |

## Fixture strategy

Unit tests MUST NOT require the 670 MB artifact. Fixtures are committed small JSON files under
`tests/fixtures/judgements/`, each a byte-exact copy of one extracted source entry, with its
expected size and CRC32 recorded in a manifest. The artifact is exercised only by explicitly
marked integration tests.

---

## Phase 1: Foundation — artifact, decoder, inventory

*No dependencies. Tasks T001, T002, T004 are parallelizable.*

### T001 — Artifact identity record

- **Layer**: [A] 2
- **Purpose**: make the artifact's identity a checked-in fact and a runtime precondition, not a filesystem accident.
- **Dependencies**: none
- **Files**: `ingest/judgements/artifact.py` (new)
- **Input**: path to `data/judgements/raw/*.rar`
- **Output**: `identify(path) -> {name, size_bytes, sha256}` plus `EXPECTED_SHA256` constant
- **Acceptance**:
  - SHA-256 of the artifact equals `ef35ce4401d7d751bbe7cbeab93cb92949f5971a26947cbde80c64e9722fea4c`
  - `size_bytes` equals 297,279,556
  - a `verify()` call raises when the digest differs from the expected value
- **Verification**: `.venv/bin/pytest -q tests/test_judgement_artifact.py` — includes a mutation test that appends one byte to a **copy** and asserts `verify()` fails
- **FR**: FR-007, FR-008 · **INV**: INV-PROV
- **Note**: the artifact is never opened for writing; tests use a temp copy for mutation

### T002 — Raw directory write-guard

- **Layer**: [A] 1
- **Purpose**: make it structurally impossible for any pipeline stage to write into `data/judgements/raw/`.
- **Dependencies**: none
- **Files**: `ingest/judgements/artifact.py`
- **Input**: a destination path
- **Output**: `assert_outside_raw(path)` raising on any resolved path inside `raw/`
- **Acceptance**: a path resolving inside `raw/` raises; a sibling path (`data/judgements/work/`) does not; `../` traversal into `raw/` is rejected after resolution
- **Verification**: `.venv/bin/pytest -q tests/test_judgement_artifact.py -k raw_guard`
- **FR**: FR-006 · **INV**: INV-SRC

### T003 — Decoder provenance record

- **Layer**: [D] 3
- **Purpose**: record decoder identity as part of ingestion provenance, so a derived corpus can prove which decoder produced it.
- **Dependencies**: none
- **Files**: `ingest/judgements/decoder.py` (new)
- **Input**: decoder binary path + version string
- **Output**: `DecoderInfo{name, version, source_url, source_sha256}` with pinned constants for UnRAR 7.13
- **Acceptance**:
  - `source_sha256` equals `72a9ccca146174f41876e8b21ab27e973f039c6d10b13aabcb320e7055b9bb98`
  - version string matches `UNRAR 7.13`
  - a `describe()` call returns the record without executing the binary
- **Verification**: `.venv/bin/pytest -q tests/test_judgement_decoder.py`
- **FR**: FR-008 · **INV**: INV-PROV, INV-REPRO
- **Note**: no decoder is selected, installed, or downloaded here. T003 records a proven configuration only

### T004 — Container inventory from headers

- **Layer**: [D] 4
- **Purpose**: enumerate entries without extracting content, so the corpus is describable with or without a decoder.
- **Dependencies**: none
- **Files**: `ingest/judgements/inventory.py` (new)
- **Input**: artifact path
- **Output**: ordered `[{path, unpacked_size, packed_size, method, is_dir}]`
- **Acceptance**: output is byte-identical across two consecutive runs; nothing is written under `raw/`; backslash-separated paths round-trip unchanged
- **Verification**: `.venv/bin/pytest -q tests/test_judgement_inventory.py` against a **fixture** RAR in `tests/fixtures/`; plus an integration test marked `@pytest.mark.judgement_corpus` that asserts 108,547 entries and runs only when the artifact is present
- **FR**: FR-007 · **INV**: INV-REPRO
- **Parallelizable**: yes — no dependency on T001/T002/T003

**Checkpoint**: the raw boundary is described, guarded, and attributable. Nothing has read content.

---

## Phase 2: Decode and verify — entry-level integrity

### T005 — Single-entry extraction with size+CRC32 verification

- **Layer**: [D] 5
- **Purpose**: extract one entry to a temp directory and prove the bytes are authentic before any parsing.
- **Dependencies**: T001, T002, T003
- **Files**: `ingest/judgements/extract.py` (new)
- **Input**: artifact path, one entry path, temp destination
- **Output**: `extract_one(entry_path) -> {bytes, size, crc32}` with `crc32` compared against the archive's recorded value
- **Acceptance**:
  - extracted length equals the archive-reported size
  - computed CRC32 equals the archive-reported CRC32, formatted `%08X`
  - the destination never resides under `raw/`
  - the artifact digest is unchanged after extraction
- **Verification**: fixture RAR in `tests/fixtures/`; the integration test asserts the known-good entry `202607/三重簡易庭刑事/SJEM,115,重秩,54,20260716,1.json` yields 3,967 bytes with SHA-256 `65cd7d32c13550adf8a36d7db2885266c295d12d18743f6e2cc8ed170755531a`
- **FR**: FR-006, FR-007 · **INV**: INV-PROV, INV-SRC
- **Note**: every extracted entry is size- and CRC32-verified. A mismatch aborts the pipeline

### T006 — Corpus sample manifest

- **Layer**: [D] 4
- **Purpose**: pin the deterministic 494-entry survey sample so future schema drift is detectable and the evidence remains reproducible.
- **Dependencies**: T004
- **Files**: `tests/fixtures/judgements/sample_manifest.json` (new), `ingest/judgements/selection.py` (new)
- **Input**: artifact path
- **Output**: the 494 entry paths with stratum tags, whose SHA-256 is `280b95bbdac0bb933568f55297ea352022457600851847b7ca955db3f0c2d8a3`
- **Acceptance**: regenerating the sample from the documented strata rules reproduces the same 494 paths and the same manifest digest; the selection is a strict subset of the inventory
- **Verification**: `.venv/bin/pytest -q tests/test_judgement_selection.py` — asserts manifest digest equality, subset property, and no duplicates
- **FR**: FR-009 · **INV**: INV-REPRO
- **Note**: selection scope (how many of 108,409 to ingest) is a **separate decision task**, not folded in here

### T007 — Selection scope decision

- **Layer**: [D] 4
- **Purpose**: decide which judgments enter the corpus. This is a scoping decision, not an implementation detail.
- **Dependencies**: T006
- **Files**: `specs/004-judicial-source-fidelity/scope.md` (new)
- **Input**: inventory, corpus characteristics
- **Output**: a written scope (courts, date range, count) with rationale and re-run command
- **Acceptance**: `scope.md` states court set, date range, and expected entry count; applying it to the inventory yields exactly that count
- **Verification**: `.venv/bin/pytest -q tests/test_judgement_selection.py -k scope_matches_doc`
- **FR**: FR-009
- **Note**: requires a maintainer decision. T006 must complete first; **T008+ do not depend on T007's outcome** and may proceed in parallel

**Checkpoint**: bytes are extractable and verifiable; the corpus is described but not yet ingested.

---

## Phase 3: Schema contract

### T008 — Schema validator

- **Layer**: [D] 5
- **Purpose**: enforce the 8-field contract and report drift rather than absorbing it.
- **Dependencies**: T005
- **Files**: `ingest/judgements/schema.py` (new)
- **Input**: parsed JSON object + source `JID`
- **Output**: `validate(doc) -> Valid | Drift(reason)`, never an exception on unexpected shape
- **Acceptance**:
  - a conforming document reports `Valid` (key set, order, all-string, no nulls)
  - `JID` is accepted in **both** 5-field and 6-field shapes
  - an empty `JPDF` is **not** an error
  - a document with a missing key, a non-string value, or a null returns `Drift` with a specific reason
- **Verification**: fixture corpus covering: a 6-field civil ruling, a 6-field criminal ruling, a 5-field Constitutional Court ruling with empty `JPDF`, the single-line no-break ruling, and the `JYEAR="79"` anomaly
- **FR**: FR-024, FR-030, FR-B02 · **SC**: SC-019

### T009 — Canonical judgment document model

- **Layer**: [A] 6
- **Purpose**: define the immutable in-memory record carrying source fields verbatim.
- **Dependencies**: T008
- **Files**: `ingest/judgements/document.py` (new)
- **Input**: validated JSON + provenance
- **Output**: immutable record exposing `jid` (opaque), `jyear`, `jcase`, `jno`, `jdate`, `jtitle`, `jfull`, `jpdf`, plus `entry_path` and `sha256`
- **Acceptance**:
  - every stored string equals the source value byte-for-byte
  - `jid` exposes **no** positional accessor — `.jid` returns the whole string
  - the record is frozen; attribute assignment raises
  - no derived or normalized field exists on the model
- **Verification**: `.venv/bin/pytest -q tests/test_judgement_document.py` — asserts no field is renamed, `jid` has no `.year`/`.case`/`.no` properties, mutation raises
- **FR**: FR-002, FR-016, FR-028 · **INV**: INV-SRC, INV-SEP
- **Note**: `jyyear`→`jdate` derivation is **not** implemented (FR-029 forbids it)

---

## Phase 4: Lossless text representation

### T010 — Byte-exact text preservation

- **Layer**: [A] 7
- **Purpose**: guarantee `JFULL` reaches storage byte-identical, with offsets stable.
- **Dependencies**: T009
- **Files**: `ingest/judgements/text.py` (new)
- **Input**: `JFULL` string + its UTF-8 byte length
- **Output**: `LosslessText` exposing `raw`, `text`, `byte_len`, and `slice(start, end)` returning the exact substring
- **Acceptance**:
  - CRLF count is unchanged (fixture with 56 CRLF pairs → 56)
  - U+3000 count is unchanged (fixture with 264-of-corpus ratio preserved)
  - `slice(s,e)` equals `text[s:e]` for every probed offset, including multi-byte boundaries
  - there is **no** normalise/trim/collapse method on the object
- **Verification**: `.venv/bin/pytest -q tests/test_judgement_text.py` — asserts CRLF and U+3000 preservation by count, byte length stability, and 200 random offset round-trips
- **FR**: FR-002, FR-003, FR-036 · **INV**: INV-SRC
- **Note**: explicitly asserts that `ingest/laws/normalize.py::_clean`'s per-line `strip()` is **not** reused

### T011 — Section markers are not assumed

- **Layer**: [D] 7
- **Purpose**: record that marker vocabulary varies, so no downstream stage assumes one.
- **Dependencies**: T010
- **Files**: `ingest/judgements/text.py`, `tests/test_judgement_text.py`
- **Input**: `LosslessText`
- **Output**: `observed_markers(text) -> list[str]` returning only markers literally present; no fixed vocabulary
- **Acceptance**: a fixture with no recognised marker returns an empty list rather than raising or guessing; markers retain their full-width spacing (`主　　　文` ≠ `主文`); the function performs no normalisation
- **Verification**: `.venv/bin/pytest -q tests/test_judgement_text.py -k marker`
- **FR**: FR-031 · **INV**: INV-SRC

**Checkpoint**: authoritative text is byte-exact and its structure is measured, not assumed.

---

## Phase 5: Citation Candidate Detection (FR-B05 Stage A)

### T012 — Citation pattern definition

- **Layer**: [D] 8
- **Purpose**: define the verified structural pattern set as data, not scattered regex.
- **Dependencies**: T010
- **Files**: `ingest/judgements/citation_patterns.py` (new)
- **Input**: survey evidence
- **Output**: named patterns covering `第X條`, `第X條之Y`, `第X條第Y項`, `第X項第Z款`, `第X項前段`, `第X條前段`, in **both** numeral systems
- **Acceptance**: a test corpus containing `第四十四條` and `第44條` matches both; `系爭契約第一條` matches the pattern (it is a *candidate*, not a statute citation); the module exports no gazetteer and no statute-name list
- **Verification**: `.venv/bin/pytest -q tests/test_judgement_citation_patterns.py`
- **FR**: FR-033 · **SC**: SC-016
- **Note**: Chinese-numeral support is mandatory — 391 occurrences in the survey sample

### T013 — Candidate detector

- **Layer**: [D] 8
- **Purpose**: emit citation-like occurrences as offsets plus text, with no identity assignment.
- **Dependencies**: T012
- **Files**: `ingest/judgements/citations.py` (new)
- **Input**: `LosslessText`, document identity, provenance
- **Output**: `[{matched_text, start_offset, end_offset, source_document, provenance, classification}]` where `classification` starts as `candidate`
- **Acceptance**:
  - for every candidate, `matched_text == text[start_offset:end_offset]` — asserted inside the function, not only in tests
  - offsets are **character** offsets into the authoritative text and are unaffected by CRLF
  - no output field carries a statute name
  - the module contains no lookup table, no gazetteer, and no resolver entry point
- **Verification**: `.venv/bin/pytest -q tests/test_judgement_citations.py` — property test asserting the substring invariant across all candidates and all fixtures
- **FR**: FR-034, FR-035, FR-037, FR-039 · **INV**: INV-SUB, INV-NOFAB
- **Note**: `citations.py` MUST document in its module docstring that resolution is deliberately absent, or a later contributor will add statute lookup and reintroduce fabrication

### T014 — Candidate classification states

- **Layer**: [D] 8
- **Purpose**: represent uncertainty explicitly rather than collapsing it.
- **Dependencies**: T013
- **Files**: `ingest/judgements/citations.py`
- **Input**: a detected candidate
- **Output**: `classify(candidate) -> candidate | statute-citation | ambiguous | non-statute`, defaulting to `candidate`
- **Acceptance**: a doc-internal reference (`系爭契約第一條`) can be marked `non-statute`; an elided-name reference (`第65條` after `依社會秩序維護法第45條`) can be marked `ambiguous`; the default is never `statute-citation`; classification never invents a statute name
- **Verification**: `.venv/bin/pytest -q tests/test_judgement_citations.py -k classif`
- **FR**: FR-038, FR-039 · **INV**: INV-NOFAB
- **Note**: classification is a **label**, never authoritative judicial metadata. It does not attempt the 11% unresolvable cases

**Checkpoint**: FR-B05 Stage A complete. **Stage B (resolution) is deliberately NOT tasked** — see §Deferred.

---

## Phase 6: Chunking

### T015 — Lossless chunking

- **Layer**: [D] 9
- **Purpose**: cut authoritative text into retrievable units without altering it.
- **Dependencies**: T010
- **Files**: `ingest/judgements/chunk.py` (new)
- **Input**: `LosslessText`, target size
- **Output**: `[{text, start_offset, end_offset, chunk_index}]`
- **Acceptance**:
  - every chunk satisfies `text == text_full[start:end]`
  - concatenating chunks in order reproduces the source exactly, byte for byte
  - splitting points fall on existing boundaries where available; when none exists the nearest boundary is used **and recorded**
  - no LLM, no summarisation, no rewriting; embedding output is never content
- **Verification**: `.venv/bin/pytest -q tests/test_judgement_chunk.py` — reconstruction equality on all fixtures plus a 770 KB oversized fixture
- **FR**: FR-011, FR-012, FR-013 · **INV**: INV-SUB, INV-SRC

---

## Phase 7: Persistence

### T016 — Judgment metadata store

- **Layer**: [D] 10
- **Purpose**: persist canonical documents with idempotent upsert and explicit deletion semantics.
- **Dependencies**: T009, T010
- **Files**: `ingest/judgements/pg_schema.sql`, `ingest/judgements/pg_load.py` (new)
- **Input**: canonical documents
- **Output**: judgment tables carrying `JID` as primary key plus the 7 source fields verbatim
- **Acceptance**: re-running load over unchanged documents inserts 0 rows and changes 0 rows; `JID` is stored as an opaque `TEXT`; no statute/judgment cross-table confusion
- **Verification**: `.venv/bin/pytest -q tests/test_judgement_pg_schema.py` — static DDL assertions (no test touches PostgreSQL, per repo convention)
- **FR**: FR-014, FR-015, FR-024 · **INV**: INV-PROV, INV-SEP
- **Note**: **deletion semantics require a maintainer decision.** Spec 002's "never delete" does not transfer — a judgment can be vacated or republished. Recorded in `plan.md` §Remaining blockers; this task defines the table, the delete policy is a separate decision

### T017 — Judgment index load

- **Layer**: [D] 10
- **Purpose**: load chunks into a dedicated Qdrant collection.
- **Dependencies**: T015, T016
- **Files**: `ingest/judgements/qdrant_load.py` (new)
- **Input**: chunks + documents
- **Output**: points in collection `judgements` with payload `{entry_path, jid, chunk_index, start_offset, end_offset, content_hash, jyear, jdate, jcase}`
- **Acceptance**: point IDs are deterministic, so re-running overwrites rather than duplicates; payload contains **no statute identity**; `jyear`/`jdate` are stored as separate source values
- **Verification**: `.venv/bin/pytest -q tests/test_judgement_qdrant_payload.py` — static payload assertions; no live Qdrant required
- **FR**: FR-013, FR-014 · **INV**: INV-PROV, INV-REPRO
- **Parallelizable**: yes, relative to T018

---

## Phase 8: Retrieval and answer path

### T018 — Judgment retrieval

- **Layer**: [D] 11
- **Purpose**: retrieve source chunks only.
- **Dependencies**: T017
- **Files**: `backend/app/retrieve.py` (extend, not replace)
- **Input**: a question
- **Output**: hits carrying payload and offsets, from collection `judgements`
- **Acceptance**: results are source chunks, never generated text; statute/judgment collections are separable by filter; the existing statute path is untouched
- **Verification**: `.venv/bin/pytest -q tests/test_judgement_retrieve.py` — static filter/field assertions
- **FR**: FR-011, FR-023 · **INV**: INV-SEP
- **Note**: reuses the existing `COLLECTION` env boundary and hybrid dense+sparse path

### T019 — Answer policy: quote, never rewrite

- **Layer**: [D] 12
- **Purpose**: present retrieved source text verbatim with citation.
- **Dependencies**: T018
- **Files**: `backend/app/rag.py` (extend, not replace)
- **Input**: retrieved chunks + question
- **Output**: an answer composed of quoted source blocks with citation
- **Acceptance**: quoted text equals the stored source substring; **0** model-generated sentences appear inside a source block; existing statute behaviour is unchanged
- **Verification**: `.venv/bin/pytest -q tests/test_judgement_answer_policy.py`
- **FR**: FR-018, FR-019, FR-021 · **INV**: INV-NOFAB, INV-SUB

### T020 — Refusal when no source supports the question

- **Layer**: [D] 12
- **Purpose**: make "no supporting source" a structural outcome.
- **Dependencies**: T019
- **Files**: `backend/app/rag.py`
- **Input**: a question with no supporting retrieval
- **Output**: an explicit statement that no supporting source text was found, with no legal conclusion
- **Acceptance**: the response contains **0** statute text and **0** case numbers; the refusal is not presented as a successful answer
- **Verification**: `.venv/bin/pytest -q tests/test_judgement_refusal.py` over a curated no-answer question set
- **FR**: FR-020 · **SC**: SC-005
- **Parallelizable**: yes, relative to T021

---

## Phase 9: Provenance and presentation

### T021 — Provenance chain

- **Layer**: [D] 13
- **Purpose**: make every derived object traceable end to end.
- **Dependencies**: T013, T015
- **Files**: `ingest/judgements/provenance.py` (new)
- **Input**: any derived object
- **Output**: `{artifact_sha256, entry_path, json_field, start_offset, end_offset}`
- **Acceptance**: every chunk and every candidate resolves to all five components; a missing component is an error, not a default
- **Verification**: `.venv/bin/pytest -q tests/test_judgement_provenance.py`
- **FR**: FR-017 · **INV**: INV-PROV

### T022 — Source citation rendering

- **Layer**: [D] 12
- **Purpose**: show the user where an answer came from.
- **Dependencies**: T021
- **Files**: `frontend/src/routes/+page.svelte` (extend, not replace)
- **Input**: an answer with citation
- **Output**: visible court/case/date/source reference — sourced from existing source fields only
- **Acceptance**: citation fields come from stored source values, never inferred; **no court inference** from `JFULL` line 0 (that is UNKNOWN/INFERRED and forbidden as a contract); citation is visually distinct from model commentary
- **Verification**: `.venv/bin/pytest -q tests/test_frontend_judgement_citation.py` — static source assertions, matching existing frontend test convention
- **FR**: FR-017, FR-021, FR-022 · **INV**: INV-SEP
- **Note**: no personal-name masking is added (FR-032). Any access control is a separate presentation decision

### T023 — Integrity invariant suite

- **Layer**: [A] 14
- **Purpose**: one executable suite asserting every invariant end to end.
- **Dependencies**: T015, T013, T021
- **Files**: `tests/test_judgement_fidelity_rules.py` (new)
- **Input**: fixtures + derived outputs
- **Output**: a suite asserting all six invariants
- **Acceptance**: INV-SRC, INV-SUB, INV-PROV, INV-NOFAB, INV-REPRO, INV-SEPROV each have at least one assertion; INV-REPRO is proven by running a stage twice and comparing digests
- **Verification**: `.venv/bin/pytest -q tests/test_judgement_fidelity_rules.py`
- **FR**: FR-003, FR-024 · **SC**: SC-001, SC-013, SC-014, SC-018
- **Note**: follows `test_three_layer_consistency.py`'s static-rule-audit pattern

### T024 — Golden dataset

- **Layer**: [A] 4
- **Purpose**: fixed source records that fail loudly on unintended pipeline change.
- **Dependencies**: T005
- **Files**: `tests/fixtures/judgements/golden/` (new)
- **Input**: a small set of extracted source entries
- **Output**: golden JSON + expected digests, **copied verbatim from the artifact — never hand-authored**
- **Acceptance**: each golden file matches the archive's size and CRC32; running the pipeline twice yields byte-identical output; the set covers a civil ruling, a criminal ruling, and a 5-field Constitutional Court ruling
- **Verification**: `.venv/bin/pytest -q tests/test_judgement_golden.py`
- **FR**: FR-010 · **SC**: SC-009
- **Note**: a hand-authored golden record would itself be an unsourced document and would defeat the purpose

---

## Phase 10: Documentation truth

### T025 — Supersede `ingest/cases/DESIGN.md`

- **Layer**: documentation
- **Purpose**: prevent a future contributor from implementing superseded rules still present on disk.
- **Dependencies**: none
- **Files**: `ingest/cases/DESIGN.md` (annotate; do not rewrite)
- **Input**: `plan.md` §6.1 obsolete list
- **Output**: each obsolete rule marked with its superseding FR
- **Acceptance**: §8.2 boilerplate stripping, §8.3 LLM 要旨 extraction, and §8.3 court-regex are each marked obsolete with a reason; §8.2 PII check is marked deferred rather than forbidden
- **Verification**: `.venv/bin/pytest -q tests/test_judgement_fidelity_rules.py -k design_doc` — asserts each obsolete marker is present
- **FR**: FR-002, FR-005
- **Parallelizable**: yes

### T026 — Constitution principle

- **Layer**: documentation
- **Purpose**: record the immutability principle so it binds future work beyond this feature.
- **Dependencies**: none
- **Files**: `.specify/memory/constitution.md` (amend)
- **Input**: spec FR-026
- **Output**: a principle stating judicial source text is immutable, model output is not authoritative judicial content, answers retain provenance, and retrieval does not alter source content
- **Acceptance**: the principle appears in the constitution with a version bump; Principle VI (RAG Correctness) is cross-referenced rather than duplicated
- **Verification**: `.venv/bin/pytest -q tests/test_judgement_fidelity_rules.py -k constitution`; plus the Sync Impact Report required by the constitution's own process
- **FR**: FR-026
- **Parallelizable**: yes

---

## Deferred — no task created

**FR-B05 Stage B — Citation Resolution.** Intentionally untasked. `citation-pattern-survey.md` proved that 11% of article references do not resolve to a statute identity, and that the local gazetteer covers 3.9% of observed names and is stale. Any resolver would guess. It requires an independent, verifiable gazetteer or contextual contract — a precondition this feature does not meet. Spec FR-040…FR-043 govern it when it is taken up.

**Layer 15 — security/privacy boundary.** Unmasked personal names are preserved by ingestion (FR-032). Access control, redaction, and log policy are a presentation-layer decision that has not been made.

**Layer 16 — `court` resolution.** No authoritative court field exists. Inferring one from `JFULL` line 0 is forbidden as a contract.

---

## Dependencies & Execution Order

```
Phase 1   T001 ┐
          T002 ├─ Parallelizable (no deps)
          T003 ┤
          T004 ┘
             │
Phase 2   T005 (needs T001,T002,T003) ── T006 ── T007
             │
Phase 3   T008 ── T009
             │
Phase 4   T010 ── T011
             │
Phase 5   T012 ── T013 ── T014        ← FR-B05 Stage A only
             │
Phase 6   T015 (needs T010)
             │
Phase 7   T016 (T009,T010) ┐
          T017 (T015,T016)  ├─ T017 parallelizable w.r.t. T018
             │              ┘
Phase 8   T018 ── T019 ── T020        ← T020 parallelizable w.r.t. T021
             │
Phase 9   T021 (T013,T015) ── T022 ── T023 (T015,T013,T021)
             │
          T024 (T005)
          T025 ┐  Parallelizable — documentation only, no code deps
          T026 ┘
```

### Phase dependencies

| Phase | Depends on | Blocks |
|---|---|---|
| 1 Foundation | — | 2 |
| 2 Decode/verify | 1 | 3, 9(T024) |
| 3 Schema | 2 | 4, 7(T016) |
| 4 Lossless text | 3 | 5, 6, 7(T017) |
| 5 Citations | 4 | 9(T021, T023) |
| 6 Chunking | 4 | 7(T017), 9 |
| 7 Persistence | 4, 6 | 8 |
| 8 Retrieval | 7 | 9 |
| 9 Provenance/UI | 5, 6, 8 | — |
| 10 Documentation | — | — |

### Parallelizable tasks

`T001` `T002` `T003` `T004` (Phase 1) · `T017` w.r.t. `T018` · `T020` w.r.t. `T021` · `T025` `T026` (independent, documentation)

### Critical path

`T001 → T005 → T008 → T009 → T010 → T015 → T017 → T018 → T019 → T021 → T022`

---

## Scope boundary

**In scope**: `ingest/judgements/` (new) · `backend/app/retrieve.py` and `rag.py` (extend only) · `frontend/src/routes/+page.svelte` (extend only) · `tests/test_judgement_*.py` and fixtures (new) · `ingest/cases/DESIGN.md` (annotate) · `.specify/memory/constitution.md` (amend)

**Out of scope**: Composer · console · component registry · plugin system · any speculative abstraction · citation resolution · court inference · gazetteer construction · personal-name masking · boilerplate stripping · section rewriting · whitespace collapsing · `JYEAR`→`JDATE` inference · `JID` positional parsing · committing the production RAR to git

---

## Post-authoring consistency review

| Check | Result |
|---|---|
| Every FR maps to ≥1 task | **PASS** — 48 FRs; FR-001…FR-027 via T005/T010/T013/T015/T016/T018/T019/T020/T021/T022/T023/T025/T026; FR-028…FR-032 via T009/T008/T011/T022; FR-033…FR-039 via T012/T013/T014; FR-040…FR-043 deferred; FR-B01…FR-B04 via T001/T008/T005/T010 |
| Every invariant has a verification task | **PASS** — each is asserted by ≥4 tasks and consolidated in T023: INV-SRC (T002,T005,T009,T010,T011,T015), INV-SUB (T013,T015,T019,T023), INV-PROV (T001,T003,T005,T016,T017,T021,T023), INV-NOFAB (T013,T014,T019,T023), INV-REPRO (T003,T004,T006,T017,T023), INV-SEP (T009,T016,T018,T022,T023) |
| No task spans multiple architecture layers | **PASS** — each task names exactly one layer; T007 spans none (decision only) |
| No giant task | **PASS** — largest is T008 with 5 acceptance bullets, all one concern |
| No task introduces deferred citation resolution | **PASS** — T012/T013/T014 emit offsets and text only; `citations.py` has no resolver entry point |
| No task modifies authoritative source | **PASS** — T002 guards it; every other task reads `[A]` layers |
| No task depends on unverified court mapping | **PASS** — T022 explicitly forbids court inference |
| Unit verification does not require the 670 MB corpus | **PASS** — fixtures in `tests/fixtures/judgements/`; corpus tests marked `@pytest.mark.judgement_corpus` |
| Task touches only its declared layer | **PASS** — T025/T026 are documentation-only and declare no code |

### Deliberate scope omissions

- **No gazetteer task.** Building one would require adjudicating ~1,600 observed name strings, and the existing one is stale.
- **No court-resolution task.** There is no authoritative source for it.
- **No deletion-policy implementation.** T016 defines the table; the policy is a maintainer decision recorded in `plan.md`.
- **No PII handling task.** FR-032 forbids masking at ingestion; presentation-layer control is undecided.