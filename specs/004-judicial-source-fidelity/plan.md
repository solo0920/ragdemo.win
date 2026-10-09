# Implementation Plan: Judicial Source Fidelity

**Branch**: `main` | **Date**: 2026-10-07 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/004-judicial-source-fidelity/spec.md`

**Status**: Planning only. No production code modified, nothing extracted to `raw/`, nothing
ingested, no migration, nothing committed.

---

## 0. Corrections to the stated premises

Two premises in the task did not match the filesystem. Both were verified before planning, and
neither changes the feature's shape — but both invalidate instructions as written.

### 0.1 The artifact is a RAR, not a ZIP

| Stated | Actual |
|---|---|
| `data/judgements/raw/file.zip` | `data/judgements/raw/202607--(20260916Update).rar` |
| ZIP | **RAR 4 archive** (`Rar!\x1a\x07\x00`) |

`find . -name 'file.zip'` returns nothing. Consequences: the filename in every downstream
reference must come from the actual artifact; the container is RAR, which the stdlib cannot
read; and the raw-preservation rules still apply unchanged (the artifact is immutable
regardless of container format).

This is also the direct continuation of the previous investigation: the RAR fileset whose
official `resourceDescription` is `202607--(20260916Update)` is `fileSetId=70691`. **The manual
acquisition succeeded for exactly the fileset the API refused to serve.**

### 0.2 The artifact was unreadable — RESOLVED

No RAR decoder exists on any host: no `unrar`, `unar`, `lsar`, `patool`, `7z`, `7za`, `bsdtar`,
and no Python `rarfile`. **This is resolved.** See `decoder-proof.md`: UnRAR 7.13 was built from
official rarlab source in a disposable container and proved to extract this artifact.

| Property | Value |
|---|---|
| Version | UNRAR 7.13 |
| Source | `https://www.rarlab.com/rar/unrarsrc-7.1.10.tar.gz` |
| Source SHA-256 | `72a9ccca146174f41876e8b21ab27e973f039c6d10b13aabcb320e7055b9bb98` |
| Licensing | UnRAR source terms: use to handle RAR archives free of charge; redistribution permitted with the licence paragraph carried in documentation |

### 0.3 Superseded premise — a schema was assumed before it existed

An earlier draft of this plan deferred the judgment data model because the schema was unknown.
It is now **observed**, not assumed: see `corpus-schema-survey.md`. Evidence outranks that
premise.

### 0.4 Superseded premise — statute citation was assumed extractable

An earlier draft implied `JFULL` citations could be resolved to statute identities.
`citation-pattern-survey.md` proves that unsafe: **11% of article references do not resolve**, and
no available gazetteer can adjudicate them. This plan adopts **Detection ≠ Resolution**
(spec FR-033…FR-043).

---

## 1. READY NOW

Work fully specified by evidence, with no unresolved dependency.

| # | Item | Basis |
|---|---|---|
| R-1 | **Raw artifact identity + immutability** — name, size, SHA-256; `raw/` write-guard | `decoder-proof.md` §1 |
| R-2 | **Deterministic container inventory** — entry list from headers, no extraction | 108,547 entries verified twice, byte-identical |
| R-3 | **Extraction with verification** — temp dir only; each entry checked against the archive's size **and** CRC32 | 494/494 verified |
| R-4 | **Lossless text representation** — CRLF and U+3000 preserved; offsets stable | `corpus-schema-survey.md` §4 |
| R-5 | **Canonical judgment document** — 8 source fields verbatim; `JID` opaque | 1 key set across 494 samples |
| R-6 | **Citation Candidate detection** — offsets + text + provenance; both numeral systems; no statute identity | `citation-pattern-survey.md` §3 |
| R-7 | **Chunk substring invariant + test** — `chunk.text == JFULL[s:e]` | spec FR-011 |
| R-8 | **Hash chain + provenance chain** — artifact → entry → field → offset | spec FR-007/FR-008 |
| R-9 | **Refusal path (Case D)** — no legal conclusion when retrieval yields nothing | spec FR-020 |
| R-10 | **UI separation of source text from model commentary** | spec FR-021/FR-022, US4 |
| R-11 | **Constitution rule + DESIGN.md supersession notice** | spec FR-026 |

**Two deserve singling out.** R-2 needs no decoder at all — the inventory comes from container
headers, so it is reproducible regardless of tooling. R-6 is scoped by a **negative** result:
detection is safe, resolution is not, so the detector emits offsets and text and nothing else.

R-9 is unblocked because the refusal guarantee lives in the retrieval/answer path, which is
existing code — it needs no judgment data to be correct.

---

## 2. VERIFIED FROM LOCAL SOURCE

Everything below was established by reading the artifact on 2026-10-07. **No content was
extracted; no field name was inferred.**

### 2.1 Artifact identity (VERIFIED)

```
path    : data/judgements/raw/202607--(20260916Update).rar
type    : RAR archive data, v4, os: Win32
bytes   : 297,279,556
sha256  : ef35ce4401d7d751bbe7cbeab93cb92949f5971a26947cbde80c64e9722fea4c
```

### 2.2 Container inventory (VERIFIED, read-only header walk)

| Property | Value |
|---|---|
| Total entries | 108,547 |
| Content files | 108,409 |
| Directory entries (zero-byte) | 138 |
| Stray non-JSON content entry | **1** — `202607\臺灣新北地方法院民事\PCDV,115,司` (1,988 bytes) |
| Extensions | `json` × 108,408, plus the 1 stray |
| Total unpacked size | 669,514,154 bytes (**670 MB**) |
| Size min / median / max | 318 / 2,951 / 777,963 bytes |
| Nested archives | **None** — every content entry is a flat `.json` |
| Compression | 108,409 × method `0x33` (RAR-proprietary "normal"); 138 × `0x30` (store, all zero-byte) |

### 2.3 Path structure (VERIFIED)

```
202607\<court>\<CODE>,<year>,<division>,<serial>,<date>,<instance>.json
```

- 137 distinct court directories, each suffixed 民事 / 刑事
- Top courts: 臺灣臺北地方法院民事 (7,171), 臺灣桃園地方法院民事 (6,512), 臺灣臺中地方法院民事 (6,481), 臺灣新北地方法院民事 (5,830)
- Filename component 4 spans 20260701–20260731
- Path separators are Windows backslashes; the container declares host OS as Win32

**This is a filename convention, not a schema.** It suggests — does not establish — where
court, case number, and date live. §8 of the previous investigation is unchanged: the *contents*
of these JSON files remain unverified.

### 2.4 What this changes about the plan

- The volume is real: 108k judgments / 670 MB unpacked, versus 906 statutes / 29k articles
  today. Spec 002's assumptions about scale do not carry over.
- Selection scope is now a **live decision**, not an open question. `DESIGN.md` §8.1 proposed
  500–1,000 cases; the artifact supplies 108,409. Choosing a subset is a scoping requirement,
  not an accident of availability.
- The stray `PCDV,115,司` entry proves the inventory must tolerate malformed names rather than
  assume a clean pattern.

---

## 3. BLOCKED / UNKNOWN

### 3.1 BLOCKED — no RAR decoder (newly identified)

All 108,409 content entries use RAR method `0x33`, which is RAR's proprietary algorithm.
`zlib` cannot decode it (verified: `zlib.error: invalid code lengths set`). The 138 stored
entries are all zero-byte directories.

**Consequence**: the JSON schema cannot be inspected. FR-B01–FR-B05 remain BLOCKED, and the
blocking cause has changed — no longer "the source is unavailable", now "the source is present
but not yet decodable".

**Options for resolution**, none taken unilaterally:

| Option | Cost | Note |
|---|---|---|
| Install `unrar` via apt | Requires sudo; changes host state | `rarfile` (pure Python) still needs an external `unrar` binary |
| Install `unar` / `p7zip` | Same | Handles RAR 4 and 5 |
| Re-acquire as ZIP | Requires re-download; may not exist as ZIP | Would discard a verified 283 MB artifact |
| Extract once to a temp dir, discard | Same as first option | Still needs a decoder |

I have **not** installed anything. This is a host-state decision.

### 3.2 RESOLVED — JSON schema

Observed, not guessed (`corpus-schema-survey.md`): flat object, 8 string fields
`JID, JYEAR, JCASE, JNO, JDATE, JTITLE, JFULL, JPDF`, stable key order, no nulls. `JFULL` is the
sole full-text field. `JID` has two legal shapes (5- and 6-field). `JYEAR` and `JDATE` are
independent. `JPDF` is empty for Constitutional Court entries. **VERIFIED IN SAMPLE** (n=494);
**corpus-wide uniformity UNKNOWN**.

### 3.3 RESOLVED — FR-B01…FR-B04; FR-B05 restructured

FR-B01…FR-B04 are settled by evidence. FR-B05 became a **negative result**: detection is safe,
resolution is not. Stage B (resolution) is deferred and MUST NOT be implemented in this feature.

### 3.4 REMAINING UNKNOWN / INFERRED — carried into tasks

| Item | State | Consequence |
|---|---|---|
| Corpus-wide schema uniformity | UNKNOWN (0.46% sample) | Ingestion MUST detect drift (SC-019), not assume stability |
| `court` as an authoritative field | **UNKNOWN / INFERRED** | Must not enter a contract; archive path is the only reliable attribute |
| Court↔directory mapping table | UNKNOWN | Does not exist in corpus or repo |
| `JCASE` semantics (1,336 codes) | UNKNOWN | Store as opaque string |
| Statute-citation resolution | DEFERRED | Stage B only, with independent evidence |
| Personal-name handling at UI/log boundaries | UNKNOWN | Ingestion preserves verbatim (FR-002); access control is a separate decision |

---

## 4. EXTERNAL SOURCE ISSUE

Unchanged from the previous investigation, but now materially less urgent:

- The official API `/api/FilesetLists/{id}/file` serves **all 367 RAR judgment filesets** with
  HTTP 500 while CSV/7Z filesets return 200. The platform's own documented example (`1038`)
  fails identically.
- **This is now a reporting matter, not a work blocker.** The artifact in hand *is*
  `fileSetId=70691`. No further download is needed for this feature.
- It remains worth reporting to the Judicial Yuan data provider, because the next monthly
  refresh depends on either a fix or another manual acquisition.

---

## 5. Plan architecture

Each layer is labelled: **[A]** authoritative · **[D]** derived · **[I]** inferred · **[O]** optional.

```
data/judgements/raw/202607--(20260916Update).rar          [A] IMMUTABLE, never written
   │  sha256 = ef35ce4401d7d751bbe7cbeab93cb92949f5971a26947cbde80c64e9722fea4c
   ▼
1  RAW ARTIFACT BOUNDARY          [A]  raw/ is read-only; acquisition is manual & human
   ▼
2  ARTIFACT IDENTITY              [A]  name/size/sha256 recorded; hash mismatch aborts
   ▼
3  DECODER                        [D]  UnRAR 7.13, pinned source revision + sha256
   ▼
4  INVENTORY                      [D]  108,547 entries from container headers, no extraction
   │                                    138 dirs + 108,409 json + 1 stray
   ▼
5  SOURCE JSON ADAPTER            [D]  JSON → judgment document; field names verbatim
   │                                    raw bytes + hash retained; size+CRC32 verified
   ▼
6  CANONICAL JUDGMENT DOCUMENT    [A]  8 source fields; JID opaque (5- and 6-field both legal)
   ▼
7  LOSSLESS TEXT REPRESENTATION   [A]  JFULL byte-exact; CRLF + U+3000 preserved
   │                                    NO whitespace collapsing, NO offset-shifting normalisation
   ├──────────────────────────────┐
   ▼                              │
8  CITATION CANDIDATE DETECTION   [D]  offsets + matched_text + provenance ONLY
   │                                    both numeral systems; no statute identity (FR-033…039)
   │                                    substring invariant enforced per candidate
   │                              │
   ▼                              │
9  LOSSLESS CHUNKING              [D]  contiguous slices; chunk.text == JFULL[s:e]
   │                                    no LLM, no rewrite, no re-embedding as content
   ▼                              │
10 INDEX                           [D]  Qdrant collection `judgements` + PG metadata
   │                                    COLLECTION env var, point_id idempotency, sparse
   ▼                              │
11 RETRIEVAL                      [D]  returns source chunks — never generated text
   │                              │
   ▼                              │
12 SOURCE CITATION / UI           [D]  quotes candidate text + provenance
   │                                 judicial source block visually separate from AI commentary
   ▼
13 PROVENANCE                     [D]  artifact hash → entry → JSON field → byte offset
   ▼
14 INTEGRITY INVARIANTS           [A]  source / substring / provenance / no-fabrication /
                                     reproducibility / separation
   ▼
15 SECURITY & PRIVACY             [O]  access control + redaction boundary — SEPARATE layer;
                                     ingestion preserves names verbatim (FR-002)

DEFERRED — NOT IN THIS FEATURE
   ▼
16 CITATION RESOLUTION            [O]  deferred: needs an independent, verifiable gazetteer
                                     or contextual contract. MUST admit
                                     resolved/ambiguous/unresolved. MUST NOT guess. (FR-040…043)
```

### Invariants, and where they bind

| Invariant | Enforced at |
|---|---|
| **Source** — `JFULL` unchanged | 7; re-verified against archive size + CRC32 at 5 |
| **Substring** — `chunk.text == JFULL[s:e]`, `matched_text == JFULL[s:e]` | 8, 9 |
| **Provenance** — artifact hash → entry → field → offset | 13 |
| **No-fabrication** — detection emits no statute name, article meaning, summary, or conclusion | 8 |
| **Reproducibility** — same artifact + decoder + algorithm ⇒ identical output | 3, 4, 9 |
| **Separation** — authoritative text and derived metadata never merged | 6, 8 |

**Layer 15 is optional and deliberately late.** Ingestion preserves unmasked names because FR-002 forbids alteration; any masking belongs at a presentation boundary, never upstream.

### Detection ≠ Resolution

The architecture forks at layer 8. Detection is a **pure function over `JFULL`** producing offsets and text. Resolution is deferred. Nothing between layers 8 and 12 may depend on a statute identity, because none is available — 11% of article references do not resolve, and the local gazetteer covers 3.9% of observed names.

### Reuse of existing implementation

| Existing asset | Reused for | Not assumed |
|---|---|---|
| `ingest/laws/sync_daily.py` `sha256_bytes()`, version snapshots, step orchestration | artifact identity + re-ingest safety | — |
| `data/laws/raw/latest.zip` + `versions/` | precedent for immutable raw + bounded retention | — |
| `normalize.py` `_clean()` shape (BOM/CRLF/trim) | *pattern only* — its per-line `strip()` is **forbidden** for judgments (it is a semantic modification) | Judgment schema ≠ statute schema |
| `pg_load.py` idempotent upsert + `pg_schema.sql` DDL style | metadata store | Judgment tables are new; no statute table reused |
| `qdrant_load.py` `point_id()`, `chunk_idx`, `content_hash`, sparse vectors | indexing | Judgment chunking is lossless, not its char-limit split |
| `COLLECTION` env var in `retrieve.py` | collection boundary — already exists | — |
| `retrieve.py` hybrid dense+sparse + `_fusion_sort` | retrieval | — |
| `rag.py` SYSTEM prompt + refusal instruction | answer path | Prompt is **not** the enforcement mechanism (FR-002/FR-005) |
| `test_three_layer_consistency.py` static-rule-audit pattern | fidelity test style | — |
| `_BASE_FILTER` source filter in `retrieve.py` | statute/judgment separation | — |
| `tests/conftest.py` sys.path convention | test layout | — |

### Module boundary (Composer-ready, no Composer built)

```
ingest/judgements/
  artifact.py     [1][2]  artifact identity + raw/ write-guard      (decoder-free)
  inventory.py    [2][4]  container inventory from headers           (decoder-free)
  extract.py      [3][5]  temp-dir extraction, size+CRC32 verification
  adapter.py      [5][6]  JSON → judgment document; 8 fields verbatim, JID opaque
  normalize.py    [7]     technical normalization ONLY; position-recorded; offsets stable
  citations.py    [8]     Citation Candidate DETECTION — offsets + text, no identity
  chunk.py        [9]     lossless slicing + substring assertion
  qdrant_load.py  [10]    reuse laws patterns
  pg_load.py      [10]    new tables, new file
```

Each stage is a pure function over bytes → bytes plus a record. No stage imports the next
stage's internals; a future Composer can wrap these as nodes without any of them knowing.
**No Composer, console, registry, plugin system, or speculative abstraction is built.**

`citations.py` exists **because** resolution is deferred. It is a detector with no resolver, and
its module docstring must say so — otherwise a later contributor will "helpfully" add statute
lookup and reintroduce fabrication.

---

## 6. Reconciled designs — obsolete assumptions, marked and bounded

`ingest/cases/DESIGN.md` predates this spec and **contradicts** it. Marked obsolete here; the
file itself is **not** modified by this plan (see T-16). Nothing below may serve as an active
implementation contract.

### 6.1 From `ingest/cases/DESIGN.md`

| Rule | Status | Reason |
|---|---|---|
| §8.2 「去 boilerplate：當事人資訊、訴訟費用、據上論結尾巴」 | **OBSOLETE — MUST NOT IMPLEMENT** | Deleting content is a semantic modification (FR-002). A judgment missing its 主文 is worse than a verbose one. |
| §8.3 「要旨／爭點摘要用 ollama 批次補抽」 | **OBSOLETE as authoritative content** | LLM output MUST NOT be stored as judicial content (FR-005). If wanted, it belongs in a separate, visually separated, non-authoritative field (US4). |
| §8.3 「案號／法院／日期…regex 即可」 | **OBSOLETE** | VERIFIED: there is **no court field**. Court appears only in `JFULL` line 0 — in 487/494, with 7 exceptions where it is not a court at all. A regex would fabricate metadata (FR-035). |
| §8.1 「第一階段 500~1,000 件」 | **SUPERSEDED** | The artifact supplies 108,409. Scope selection is an explicit decision. |
| §8.1 資料源 candidates | **RESOLVED** | The real source is the open-data RAR artifact. |
| §8.2 「個資去識別化（判決公開本就已遮，但要再檢查）」 | **DEFERRED, not forbidden** | Verifying pre-masked PII is permitted provided nothing is altered. Note VERIFIED: names are **not** fully masked. |

### 6.2 Assumptions this plan previously made, now refuted by evidence

| Prior assumption | Status | Evidence |
|---|---|---|
| Schema unknown; cannot design a data model | **REFUTED** | 8 flat string fields, 1 key set across 494 samples |
| All `JID` are 6-field | **REFUTED** | 139 five-field Constitutional Court entries |
| `JYEAR` derivable from `JDATE` | **REFUTED** | They disagree in 80/494; independent semantics |
| Every judgment has a 主文 | **REFUTED** | 46 marker combinations; 133 entries with none; `主　　　文` in only 48/494 |
| Every `第X條` is a statute citation | **REFUTED** | 87 doc-internal refs (`系爭契約第一條`); 610 unresolvable; 19% elide the statute name |
| Statute identity is determinable from `JFULL` | **REFUTED** | Only 87% resolvable at best; gazetteer covers 3.9% of observed names and is stale |
| Arabic numerals only | **REFUTED** | 391 Chinese-numeral article refs; 38 docs use both |
| CRLF splits citations across lines | **REFUTED (favourable)** | 0 of 5,310 citations split; line-based scanning is structurally safe |

---
## 7. Implementation tasks

**Task definition is deferred to `/speckit.tasks`.** An earlier draft carried an inline task
list; it is removed here because its content was written when the schema was unknown, and several
tasks (schema discovery, decoder acquisition) are now either resolved or restructured.

The task set MUST be derived from the 15-layer architecture in section 5, and MUST split FR-B05 into:

- **Citation Candidate Detector** — in scope now (layer 8)
- **Citation Resolution** — future/optional, deferred (layer 16)

Two tasks MUST exist purely to record documentation truth, because the superseded designs are
still on disk and would otherwise be followed by a future contributor:

- mark `ingest/cases/DESIGN.md` sections 8.2/8.3 obsolete (section 6.1)
- add the judicial source-immutability principle to the constitution (spec FR-026)

---
## 8. Test strategy

Layered so that a fidelity regression is attributable to the layer that caused it
(constitution Principle VI requires retrieval and generation be measurable separately).

| Layer | Scope | Ties to |
|---|---|---|
| Unit | identity, inventory determinism, normalization allow-list, chunk substring, hash chain | AC-1, AC-2, AC-7 |
| Golden | fixed judgment fixtures through the full pipeline, byte-compared | AC-1, AC-5, SC-009 |
| Integration | artifact → inventory → extract → normalize → chunk → index → retrieve → source output | AC-3, AC-6 |
| Refusal | curated no-answer questions; assert no legal conclusion | AC-4, SC-005 |
| Static-rule audit | `tests/test_judgement_fidelity_rules.py` — asserts DESIGN.md §8.2/§8.3 rules are absent from the code, mirroring `test_three_layer_consistency.py`'s approach of auditing rules without touching services | FR-002, FR-005 |

Conventions honoured: tests under `tests/`, `conftest.py` sys.path, **no test touches external
services** — inventory/identity/chunk/normalization are all pure functions over bytes.

---

## 9. Migration and data safety

**No migration is created by this plan.**

| Concern | Position |
|---|---|
| Existing statute pipeline | Untouched. FR-023. New `ingest/judgements/`, new PG tables, new Qdrant collection. |
| Collection separation | `COLLECTION` env already provides the boundary; `judgements` is a distinct collection, not a schema change to `laws`. |
| Existing `rag.py` behaviour | Unchanged until T-14. Judgment retrieval is additive. |
| Raw artifact | Immutable. `raw/` becomes read-only by contract (T-03). |
| Re-ingest idempotency | `point_id()` pattern reused; stable point ids make re-run a no-op, matching `laws` behaviour. |
| Deletion semantics | Spec 002's lesson applies: `pg_load` never deletes, so withdrawn judgments persist. For judgments this is **worse** — a judgment can be republished or vacated. Must be decided at T-11, not inherited silently. |
| Rollback | Drop the `judgements` collection and its PG tables. The raw artifact is untouched, so re-ingest is always possible. |
| Storage | 670 MB unpacked per monthly artifact. Retention policy required before T-12. |

---

## 10. Constitution Check

*GATE: must pass before Phase 0; re-check after Phase 1 design.*

| Principle | Compliant | Assessment |
|---|---|---|
| I. Vertical Slice First (NON-NEGOTIABLE) | ✅ | Each task yields a verifiable increment; T-01…T-06 ship working capability without any adapter |
| II. Stable Core, Replaceable Components (NON-NEGOTIABLE) | ✅ | JSON is one adapter among possible formats; no format logic leaks past `[4]` |
| III. Simplicity Over Speculation (NON-NEGOTIABLE) | ✅ | No Composer, console, registry, plugin system, or speculative abstraction. 7 focused modules |
| IV. Contract Before Implementation (NON-NEGOTIABLE) | ✅ | Provenance shape (R-6) and normalization rules (R-5) fixed before adapters exist |
| V. Test Behaviour, Not Implementation (NON-NEGOTIABLE) | ✅ | Every task's verification asserts an observable property — hash, substring, determinism — not a structure |
| VI. RAG Correctness and Traceability (NON-NEGOTIABLE) | ✅ | Extends this principle with an immutability guarantee; retrieval/generation measured separately |
| VII. Security and Data Boundaries (NON-NEGOTIABLE) | ✅ | No secret handling introduced; artifact access is local and read-only |
| VIII. Reproducible Development Environment (NON-NEGOTIABLE) | ⚠️ | **New dependency**: a RAR decoder, absent on all three hosts. Strategy validated; host install still pending. See Complexity Tracking |
| IX. Observability and Debuggability (NON-NEGOTIABLE) | ✅ | Stage-level records; fidelity failures distinguishable from generation failures |
| X. AI-Agent Discipline (NON-NEGOTIABLE) | ✅ | No production code modified; field names **observed, not guessed**; eight refuted assumptions marked obsolete rather than silently carried forward |

**Gate result**: PASS with one dependency noted below.

### Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| Principle VIII — new host dependency: a RAR decoder (`unrar`/`unar`/`7z`), absent on x570/mbp/wsl | All 108,409 content entries use RAR method `0x33`, undecodable by zlib or the stdlib. Verified: `zlib.error: invalid code lengths set`. **Proved working** via UnRAR 7.13 built from official source (`decoder-proof.md`) | Re-downloading as ZIP would discard a verified 283 MB artifact and may not be offered. Writing a RAR decoder is absurd. `rarfile` alone is insufficient — pure Python, shells out to an external `unrar`. `bsdtar`/libarchive supports RAR5, **not** RAR3/4, so it cannot read this archive. **Host install still pending.** |

---

## 11. Can `/speckit.tasks` proceed?

**Yes.**

All five formerly-blocked requirements now have evidence:

| Req | Evidence | Status |
|---|---|---|
| FR-B01 identity | 5- and 6-field `JID` both real → opaque string | **PASS** |
| FR-B02 field mapping | 8 flat string fields, 1 key set, n=494 | **PASS** |
| FR-B03 raw preservation | 494/494 size+CRC32 verified; hash unchanged | **PASS** |
| FR-B04 lossless handling | CRLF, U+3000, marker variance now contractual | **PASS** |
| FR-B05 citations | Restructured: Detection (in scope) ≠ Resolution (deferred) | **PASS** |

**Corpus-wide uniformity is UNKNOWN** (0.46% sample). This is not a blocker: SC-019 requires the
pipeline to *detect* drift rather than assume stability, which is the correct posture for a corpus
of 108,409 records that will change monthly.

**Carried into tasks as runtime variation, not as blockers** (§3.4): `court` UNKNOWN; `JCASE`
semantics UNKNOWN; citation resolution DEFERRED; PII display boundary UNKNOWN.

**Next**: task the remainder, splitting FR-B05 into **Citation Candidate Detector** (now) and
**Citation Resolution** (future/optional).

---

## Remaining blockers

**None that prevent task definition.** The following are decisions or external matters, and each
is carried explicitly rather than assumed:

| # | Item | Type |
|---|---|---|
| 1 | RAR decoder not yet installed on any host | **Host-state decision** — strategy validated, install pending |
| 2 | Selection scope: which of 108,409 judgments | **DECISION** (T-04) |
| 3 | PG deletion semantics — spec 002's "never delete" does not transfer safely; a judgment can be republished or vacated | **DECISION** |
| 4 | Retention for 670 MB per monthly artifact | **DECISION** |
| 5 | PII display/log boundary — ingestion preserves names (FR-002); access control is a presentation-layer decision | **DECISION** |
| 6 | The `/file` API defect persists | **EXTERNAL** — manual acquisition per refresh |

## The governing principle

> Report 「這裡有一段可能的法規引用，但目前無法可靠判定是哪一部法律」
> rather than confidently emitting a possibly-wrong statute name.

This is why FR-B05 Stage B is deferred rather than approximated.