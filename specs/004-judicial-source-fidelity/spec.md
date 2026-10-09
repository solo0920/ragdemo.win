# Feature Specification: Judicial Source Fidelity

**Feature Branch**: `004-judicial-source-fidelity`

**Created**: 2026-10-07

**Status**: Draft — source resolved; corpus contract established from three evidence documents

**Input**: User description: "建立 RAGDemo.win 的「司法判決原文保真檢索」能力 — 系統只能從司法院官方資料檢索真正存在的法條原文與司法判決原文，以原始資料內容回答；系統不得自行生成、改寫、摘要、補充或推論不存在於來源資料中的法律內容"

**Evidence base** (all in this directory; evidence outranks every prior assumption):

| Document | Result |
|---|---|
| [`decoder-proof.md`](./decoder-proof.md) | PASS — decoder extracts real Judicial Yuan data |
| [`corpus-schema-survey.md`](./corpus-schema-survey.md) | PASS — schema contract established |
| [`citation-pattern-survey.md`](./citation-pattern-survey.md) | PASS (negative) — citation detection possible, resolution is **not** safe |

---

## Source Resolution — the blocker is closed

The specification originally named this endpoint as the authoritative source:

```
https://opendata.judicial.gov.tw/api/FilesetLists/70691/file
```

Probed 2026-10-07: it returns **HTTP 500**, and the `/file` sub-path fails for every id tested, including the platform's own documented example. The host itself is alive.

**The source is nevertheless acquired.** A human operator authenticated and downloaded the fileset directly. The artifact is now the ingestion boundary.

| Property | Value |
|---|---|
| Artifact | `data/judgements/raw/202607--(20260916Update).rar` |
| `fileSetId` | 70691 |
| SHA-256 | `ef35ce4401d7d751bbe7cbeab93cb92949f5971a26947cbde80c64e9722fea4c` |
| Container | RAR 4, 297,279,556 bytes |
| Entries | 108,547 (108,409 JSON, 138 zero-byte directories) |
| Unpacked | 669,514,154 bytes |
| Acquisition | **manual, human-run, authenticated** |

**Acquisition is outside this feature's scope.** No login, token, session, CAPTCHA, browser automation, or API authentication is implemented here. The artifact is the input; the pipeline is read-only with respect to it.

The API defect remains an **external** matter: the next monthly refresh depends on either a provider fix or another manual acquisition.

### Relationship to spec 002

`002-moj-format-ingest-audit` is **not** superseded and **not** duplicated here. That spec audits statute fidelity end to end (司法院 HTML / Open API / parquet / PostgreSQL / Qdrant) for **law.moj.gov.tw** — the Ministry of Justice statute API, 906 statutes. This spec concerns a **different authority** (司法院 open data, judgments). The two share an intent; they do not share a source, a schema, or a pipeline stage.

One divergence is now load-bearing: `spec 002`'s normalization **strips each line**, which is acceptable for statute text but **destroys** the full-width-space section markers in judgments (see §Source Structure Contract). The judgment pipeline MUST NOT inherit that behaviour.

---

## Source Structure Contract — verified facts

Established by direct inspection of real source data. **Evidence outranks every prior assumption in this document.**

**Corpus-wide status**: these facts are **VERIFIED IN SAMPLE** — a deterministic 494-entry sample (of 108,409; 0.46%) spanning 3 branches, 84 courts, 170 case types, 40 dates, and the full size range. **Corpus-wide uniformity beyond the sample is UNKNOWN.** No claim below asserts exhaustive coverage of all 108,409 entries.

### Document structure — VERIFIED IN SAMPLE

| Fact | Status |
|---|---|
| Top-level JSON is a flat object | VERIFIED IN SAMPLE (494/494) |
| Exactly 8 top-level fields | VERIFIED IN SAMPLE (494/494, **1 distinct key set**) |
| Key order is stable: `JID, JYEAR, JCASE, JNO, JDATE, JTITLE, JFULL, JPDF` | VERIFIED IN SAMPLE |
| All surveyed values are strings | VERIFIED IN SAMPLE (494/494) |
| No null values observed | VERIFIED IN SAMPLE |
| `JFULL` is the sole authoritative full-text field | VERIFIED IN SAMPLE |

**The field names are observed source names. They MUST NOT be renamed, translated, or replaced by invented equivalents** (FR-016).

### Identity — VERIFIED IN SAMPLE

| Fact | Status |
|---|---|
| `JID` is a composite comma-delimited identity, equal to the archive entry basename minus `.json` | VERIFIED IN SAMPLE |
| **`JID` legitimately has TWO shapes**: 6-field (`SJEM,115,重秩,54,20260716,1`) and 5-field (`JCCC,115,審裁,1158,20260701`) | VERIFIED IN SAMPLE (139 five-field entries, all Constitutional Court) |
| **Positional parsing of `JID` is forbidden.** Any identity contract MUST key on the whole string | REQUIRED |
| `JYEAR` (case year) and `JDATE` (release batch date) are **independent**; they disagree in 80/494 | VERIFIED IN SAMPLE |
| Neither may be derived from the other (FR-029) | REQUIRED |
| `JPDF` may be empty — 139/139 Constitutional Court entries have no PDF, 355/355 others do | VERIFIED IN SAMPLE |

### Source text structure — VERIFIED IN SAMPLE, and immutable

| Fact | Status |
|---|---|
| `JFULL` uses **CRLF** line endings (493/494) | VERIFIED IN SAMPLE |
| Full-width spaces (U+3000) carry **structural** indentation, present in 264/494 | VERIFIED IN SAMPLE |
| Section markers are **not uniform** — 46 distinct combinations, 133 entries with none of the recognised markers | VERIFIED IN SAMPLE |
| `主　　　文` appears in only 48/494 | VERIFIED IN SAMPLE |
| **Whitespace collapsing, per-line stripping, or CRLF→LF conversion that changes offsets is FORBIDDEN** | REQUIRED (FR-002, FR-036) |

### Court — NOT an authoritative field

- **VERIFIED**: there is **no court field** in the JSON.
- **VERIFIED**: a court name appears in `JFULL` line 0 in 487/494 — but 7 exceptions include one where line 0 is *case commentary* (`因本件為對不公開案件聲請停止執行，故本件裁定不公開。`).
- **VERIFIED**: line 0 conflates court and document type (`民事裁定`, `高等裁定`, `支付命令`, …); 54 entries show chamber→parent relationships.
- **UNKNOWN**: no court↔directory mapping exists in the corpus or this repository.
- **Position**: `court` remains **UNKNOWN / INFERRED** and MUST NOT enter an authoritative contract (FR-016).

### Source anomalies — preserved, never corrected

| Anomaly | Count | Rule |
|---|---|---|
| Non-public case, entire judgment one sentence, no line breaks | 1 | MUST round-trip unchanged |
| `JYEAR == "79"` (ROC 1990 case in a ROC 115 release batch) | 1 | MUST NOT be auto-corrected |
| Empty `JPDF` (Constitutional Court class) | 139 | Structural, not an error |

**FR-002 governs all three.** The pipeline reports them; it does not repair them.

---

## Problem Statement

A user asks a legal question. The system's answer is generated prose. Nothing in the answer is obliged to match the text that a court or the Judicial Yuan actually published, and nothing lets the user confirm it does. In a legal context this is the worst possible failure mode: the output is fluent, plausible, and unverifiable.

The existing product already claims this capability — its prompt says 「法規判決檢索助理」 and its citation renderer prints case numbers — but the underlying judgment data has never existed. So the gap is not a tuning problem; it is an absent guarantee.

This feature establishes that guarantee as a structural property of the system rather than an instruction in a prompt. A prompt asking a model not to fabricate is advice. A stored source that cannot be silently altered, plus a chunk that must be a substring of it, plus a hash chain back to what the Judicial Yuan served, is enforcement.

**The value proposition is accuracy, traceability, source fidelity, and reproducibility — not fluent answers.** Where those conflict, source text wins, even when the source text is obviously flawed.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Source Text Cannot Be Silently Altered (Priority: P1)

A user retrieves a statute article or a judgment. The text they receive is byte-identical to what the Judicial Yuan published — not a cleaned-up, re-punctuated, or summarized version of it. If the official text contains an error, an oddity, or reads badly, it appears that way. A reviewer who compares the answer against the official site finds no difference, including in line breaks, item numbering, and internal spacing.

**Why this priority**: This is the load-bearing invariant. Every other story is downstream of it: traceability is meaningless if the text can drift, and the no-hallucination guarantee is unenforceable if the substrate itself is mutable. If only this story ships, the system is already more trustworthy than today, because what it shows is at least verifiably what it received.

**Independent Test**: Take a fixed sample of source text, run it through the entire pipeline, and assert the stored text is character-for-character identical to the retrieved text. Inject a deliberately malformed sample (odd spacing, mixed line endings, a stray byte-order mark) and confirm it survives intact rather than being tidied.

**Acceptance Scenarios**:

1. **Given** a retrieved article whose official text contains an apparent typo, **When** the user views it, **Then** the typo is present exactly as published; the system does not correct it.
2. **Given** source text containing internal line breaks that carry structural meaning, **When** it passes through the pipeline, **Then** those line breaks survive, because the existing five-layer audit (spec 002) established that collapsing them destroys item/paragraph counts.
3. **Given** text containing a byte-order mark or mixed line endings, **When** the text is stored, **Then** the technical artifact is removed but every content character is preserved, and the removal is recorded so it can be audited.
4. **Given** any transformation applied to source text, **When** a reviewer asks why a specific character differs from the raw bytes, **Then** a recorded, technical-only explanation exists for that exact position.

---

### User Story 2 - Every Answer Traces Back to a Verifiable Source (Priority: P2)

A user reads an answer and can see exactly which document it came from — which court, which case number, which judgment date, which statute — and can follow that reference back to the Judicial Yuan. If the same source is fetched again later, the user can be told whether it changed.

**Why this priority**: Trust that cannot be independently checked is indistinguishable from trust that happens to be rewarded. Provenance makes the claim falsifiable by the user. It ranks below immutability because provenance over mutable text proves nothing.

**Independent Test**: Take any displayed answer fragment and resolve its full reference chain — source record, retrieval unit, document, external source — without consulting internal databases beyond what the user can see.

**Acceptance Scenarios**:

1. **Given** an answer containing a quoted statute article, **When** the user inspects the citation, **Then** the statute name, article number, and official source location are shown.
2. **Given** an answer containing a quoted judgment passage, **When** the user inspects the citation, **Then** the court, case number, judgment date, document type, and official source location are shown.
3. **Given** a user who follows the source reference, **When** the official page loads, **Then** they arrive at the document the system actually used, not a search results page.
4. **Given** a source that has changed since ingestion, **When** the user re-requests it, **Then** the system reports the change rather than presenting stale text as current.

---

### User Story 3 - The System Refuses to Answer Rather Than Invent (Priority: P2)

A user asks something the retrieved sources do not support. The system says so plainly and stops. It does not fill the gap from model knowledge, does not hedge into vagueness, does not answer a nearby question instead, and does not present a confident conclusion the sources do not license.

**Why this priority**: A refusal is a correct outcome, and the system must treat it as success rather than failure. This shares priority with traceability because both are what a hallucination looks like from the outside — and the current system prompt already instructs the model to reply 「沒有符合比對的法條」, an instruction that is only as strong as the prompt enforcing it. This story makes the refusal structural.

**Independent Test**: Ask questions whose answers are absent from the corpus, including plausible-sounding legal questions, and confirm the output contains no statute text, no case number, and no legal conclusion beyond the explicit statement that no supporting source was found.

**Acceptance Scenarios**:

1. **Given** a question with no supporting source in the corpus, **When** the user asks it, **Then** the system states that no supporting source text was found and offers no legal conclusion.
2. **Given** a partially supported question, **When** the system responds, **Then** it presents only the supported portion and identifies what remains unaddressed, without bridging the gap itself.
3. **Given** a question outside the corpus's subject matter entirely, **When** the user asks it, **Then** the system declines without appearing to search, rather than returning the least-unrelated result.
4. **Given** model knowledge that conflicts with a retrieved source, **When** the system responds, **Then** the source governs and no contradiction of it is asserted.

---

### User Story 4 - Model Output Is Visually Separated From Source Text (Priority: P3)

A user sees source text and any model-generated commentary in visually distinct regions, each unambiguously labelled. Nothing generated is ever presented as though it came from a court.

**Why this priority**: Lowest because it is presentational. It matters most once users notice confusion between the two, but it does not itself prevent any incorrect content. It ranks above nothing only because a correct answer wrongly labelled as source text still destroys trust.

**Independent Test**: Present an answer containing both a source passage and model commentary and confirm a reader cannot mistake one for the other, including when copied out of the interface.

**Acceptance Scenarios**:

1. **Given** an answer containing source text, **When** the user views it, **Then** source text and model analysis occupy visually separate, separately labelled regions.
2. **Given** a user who copies an answer out of the interface, **When** they paste it elsewhere, **Then** the distinction between source text and model commentary remains unambiguous.
3. **Given** an answer with source text and no model commentary, **When** the user views it, **Then** no empty or misleading commentary region is shown.

---

### Edge Cases

- **Official text contains an error or an obsolete provision.** Preserve verbatim; flag as repealed/abandoned if the source says so; never silently drop. Spec 002 established that excluding such provisions makes "this article was deleted" indistinguishable from "this system has never heard of it" — a worse failure than showing it.
- **One judgment is enormous.** Chunk it without altering it; every chunk remains a verbatim substring with a location reference back into the parent document.
- **Two judgments share a case number across different courts.** VERIFIED: case number alone is insufficient — `JID` includes a court-code prefix, but Constitutional Court entries use a different 5-field shape entirely.
- **`JID` arrives in two shapes.** 5-field and 6-field are both legal. Positional parsing would misread Constitutional Court entries. Store the string; never split it by position.
- **`JYEAR` disagrees with `JDATE`.** VERIFIED in 80/494 samples. Never reconcile them; store both.
- **`JPDF` is empty.** VERIFIED for all Constitutional Court entries. Not an error; never fabricate a URL.
- **The same article appears in statute and judgment contexts.** These are different documents and must not be merged into one apparent authority.
- **The source adds a field the system has no use for.** Preserve it in the raw record; do not invent a normalized field that could be mistaken for judicial content.
- **The source revises a document already stored.** Detect via hash; keep both versions; never overwrite in place without a recorded reason.
- **The source is unavailable at ingest time.** Record the failure and store nothing. An empty corpus that honestly says so beats a partially-populated one whose gaps are invisible.
- **Normalization cannot be proven not to alter meaning.** Do not perform it. The governing rule is that unprovable transformations are forbidden, not merely discouraged.
- **Line-break structure carries item counts.** Already established by spec 002: collapsing newlines destroys 項/款 counts. Treat as a fidelity constraint, not a formatting preference.

---

## Requirements *(mandatory)*

### Functional Requirements

#### Source fidelity (non-negotiable)

- **FR-001**: Judicial source text MUST be stored exactly as retrieved from the authoritative source, with any technical normalization recorded per-character-position rather than applied silently.
- **FR-002**: The system MUST NOT alter, rewrite, correct, paraphrase, summarize, or complete judicial source text at any stage between retrieval, storage, chunking, and display.
- **FR-003**: Where a transformation cannot be proven not to change judicial content, the system MUST NOT apply it.
- **FR-004**: The system MUST preserve line-break structure in judicial source text, because newlines carry item and paragraph counts.
- **FR-005**: The system MUST store model-generated text in a location distinct from judicial source content, and MUST NOT write model output into any store holding authoritative judicial text.

#### Raw preservation and integrity

- **FR-006**: The system MUST retain the unmodified original response from the authoritative source alongside any normalized form.
- **FR-007**: Every stored judicial document MUST carry a stable identity derived from the source, and a content fingerprint that is reproducible for identical source content.
- **FR-008**: Each stage of the pipeline — raw, normalized, chunk — MUST record a fingerprint, so that the chain from a displayed passage back to the original source bytes is verifiable.
- **FR-009**: Each chunk MUST carry its originating document identity and its location within that document, sufficient to locate the passage in the full text.
- **FR-010**: Re-ingesting unchanged source content MUST NOT produce duplicate documents.

#### Chunking

- **FR-011**: Each chunk's text MUST appear verbatim within its originating normalized document.
- **FR-012**: The system MUST NOT derive chunk text from model summarization, model rewriting, or embedding output.
- **FR-013**: Embeddings MUST be used only to locate source text, never as a substitute for it.

#### Provenance

- **FR-014**: Every retrieved passage MUST carry the metadata fields the authoritative source actually supplies for that document type. Field names MUST be taken from the source once its format is confirmed, not assumed.
- **FR-015**: Judgment-level metadata MUST include the court and, when the source provides it, the case number, judgment date, document type, and source location.
- **FR-016**: The system MUST NOT synthesize a metadata field that could be mistaken for judicial content, such as an inferred case number or a derived court name.
- **FR-017**: Every answer MUST be traceable through chunk, document, and source record to the authoritative source, and the source reference shown to the user MUST resolve to the document actually used.

#### Answer policy

- **FR-018**: Where the source supports an answer, the system MUST present the supporting source text itself, with its provenance, rather than a restatement of it.
- **FR-019**: Where statute text and judgment text both bear on the question, the system MUST present both as separate quoted source blocks with their own provenance, and MUST NOT assert a legal relationship between them that the sources do not state.
- **FR-020**: Where the corpus does not support an answer, the system MUST state that no supporting source text was found and MUST NOT provide a legal conclusion from model knowledge.
- **FR-021**: The system MUST NOT present model-generated content as judicial source text.
- **FR-022**: Where the user asks for analysis beyond what the source says, the system MUST present such analysis separately and MUST NOT merge it into the source quotation.

#### Compatibility

- **FR-023**: Existing statute ingestion, retrieval, citation, and evaluation behaviour MUST continue to function. This feature adds capabilities and constraints; it does not replace the statute pipeline.
- **FR-024**: Existing schema contracts MUST NOT be broken. Additions MUST be compatible with current data, and existing records MUST remain readable.
- **FR-025**: The system MUST preserve and display repealed and abandoned provisions rather than excluding them, consistent with spec 002's finding that exclusion makes absence indistinguishable from non-existence.

#### Governance

- **FR-026**: The constitution MUST state that judicial source text is immutable, that model output MUST NOT be stored as authoritative judicial content, that every retrieved judicial answer MUST retain provenance, and that retrieval MUST NOT alter source content.
- **FR-027**: Architecture documentation MUST record the fidelity constraint so it survives beyond this specification.

#### Verified source-field contracts (added 2026-10-07 from corpus evidence)

- **FR-028**: `JID` MUST be stored as an opaque composite string. Positional parsing is forbidden: it legitimately has 5-field and 6-field shapes, and the two encode different document classes.
- **FR-029**: `JYEAR` and `JDATE` MUST be stored as independent source values. Neither may be derived from, reconciled with, or corrected against the other — they disagree in 80 of 494 sampled entries.
- **FR-030**: `JPDF` MAY be empty. Emptiness MUST NOT be treated as an error and MUST NOT be filled with a constructed or guessed URL.
- **FR-031**: Section markers are not uniform across the corpus. Any section handling MUST be evidence-driven per document and MUST NOT assume a fixed marker vocabulary.
- **FR-032**: Unmasked personal names present in `JFULL` MUST be preserved verbatim. Redaction, masking, or abbreviation at ingestion is forbidden; any access control belongs at a separate presentation boundary.

### Requirements Resolved by Evidence — formerly blocked (FR-B01 … FR-B04)

These were originally `[BLOCKED — source unavailable]`. Each is now settled by observation.

| Req | Status | Contract |
|---|---|---|
| **FR-B01** judgment identity | **PASS** | `JID` is the identity, stored as an **opaque composite string**. Positional parsing is forbidden: it has two legal shapes, 6-field and 5-field. Case number alone is insufficient. |
| **FR-B02** field mapping | **PASS** | 8 flat string fields, stable order, no nulls (VERIFIED IN SAMPLE). Observed source names are used verbatim. |
| **FR-B03** raw preservation | **PASS** | Artifact retained with SHA-256; 494/494 extractions verified against the archive's own size **and** CRC32; raw hash unchanged across every operation. |
| **FR-B04** lossless handling | **PASS** | CRLF, U+3000 indentation, and section-marker variance are now known and are part of the contract rather than unknowns. |

**Corpus-wide caveat**: FR-B01…FR-B04 are verified against a 0.46% deterministic sample. Corpus-wide uniformity is UNKNOWN and is recorded as such.

### FR-B05 — restructured: Detection ≠ Resolution

**FR-B05** (restated): statute-citation handling MUST be split into a deterministic detection stage and an independent resolution stage. Resolution is **deferred** and MUST NOT be approximated.

The original FR-B05 required deterministic resolution of a citation to a statute identity. `citation-pattern-survey.md` proved that is **unsafe**, and this specification must not retain an assumption the evidence has overturned.

**VERIFIED findings that drove this:**

- 5,310 `第X條` occurrences across the sample; structural suffixes are stable (`第X項` 3,411 · `第X條第X項` 2,131 · `第X款` 1,357 · `第X項第X款` 767 · `第X條之Y` 760 · `第X項前段` 276 · `第X條前段` 89).
- **Both numeral systems are used for article numbers**: `第四十四條` (391×) and `第44條` (4,919×), with **38 documents using both**. An Arabic-only contract loses 7.4% silently.
- **0 citations are split across a CRLF** in the matched token — line-based scanning is structurally safe.
- **19% of article references elide the statute name**; only 87% resolve under the strongest rule tested; **610 (11%) do not resolve at all**.
- The local gazetteer (`laws_meta.jsonl`, 1,347 names) covers only **63 of 1,629** observed candidate names (3.9%) and is stale — the corpus's most-cited statute name is absent from it.

#### Stage A — Citation Candidate Detection (in scope)

- **FR-033**: The system MUST detect citation-like occurrences matching verified structural patterns, and MUST support **both** numeral systems (Arabic and Chinese). An Arabic-only detector is non-conforming.
- **FR-034**: A detected occurrence MUST be stored as: `source_document`, `matched_text`, `start_offset`, `end_offset`, `provenance`.
- **FR-035**: `matched_text` MUST satisfy the substring invariant: `matched_text == JFULL[start_offset:end_offset]`, exactly.
- **FR-036**: Detection MUST NOT modify `JFULL`, and MUST NOT change any offset by normalising line endings or whitespace. Offsets refer to the authoritative text.
- **FR-037**: Detection MUST NOT assign a statute identity. The first layer is a **Citation Candidate**, never a `Statute Citation`.
- **FR-038**: Not every `第X條` is a statutory citation. Document-internal references such as `系爭契約第一條` (87 occurrences) MUST be representable as candidates that are later classified `ambiguous` or `non-statute`, and MUST NOT be recorded as authoritative statute citations.
- **FR-039**: Each candidate MUST carry a classification state of `candidate`, `statute-citation`, `ambiguous`, or `non-statute`, defaulting to `candidate`. Classification MUST NOT be presented as authoritative judicial metadata.

#### Stage B — Citation Resolution (deferred, not in scope)

- **FR-040**: Resolution MUST be implemented as a separate, independently-evidenced stage. It is **NOT** implemented by this feature.
- **FR-041**: When implemented, resolution MUST admit the states `resolved`, `ambiguous`, and `unresolved`, and MUST NOT guess. An occurrence that cannot be reliably bound MUST remain `unresolved`.
- **FR-042**: `第339條` MUST NOT become `刑法第339条` without an independent, verifiable gazetteer or contextual resolution contract. Automatic legal-name completion is forbidden.
- **FR-043**: Resolution MUST be reproducible — same artifact + same algorithm + same gazetteer ⇒ same output.

**Stage A alone satisfies the user-facing requirement**: the citation text itself is source text, retrievable and quotable with full provenance, with zero fabrication risk.

### Invariants

- **Source invariant**: `JFULL` MUST remain the original source text at all times.
- **Substring invariant**: every chunk and every citation candidate MUST be an exact substring of `JFULL`.
- **Provenance invariant**: every derived object MUST resolve to artifact hash → archive entry → JSON field → byte offset.
- **No-fabrication invariant**: detection MUST NOT produce a statute name, article meaning, judicial summary, or legal conclusion.
- **Reproducibility invariant**: same artifact + same decoder + same algorithm MUST produce identical derived output.
- **Separation invariant**: authoritative text and derived metadata MUST be stored separately and never merged.

### Key Entities

- **Authoritative source artifact**: `202607--(20260916Update).rar` (`fileSetId=70691`), SHA-256 `ef35ce44…2fea4c`. Retained unmodified. Its authority is not delegable and is not superseded by model output.
- **Raw source record**: the unmodified bytes of one archive entry as extracted, retained so any transformation can be audited against the original.
- **Judgment document**: the parsed source JSON — a flat object of 8 string fields. `JFULL` is the authoritative full text; the other seven are identity and metadata.
- **Document identity (`JID`)**: the source-supplied composite identity, treated as an opaque string. Two legal shapes (5- and 6-field).
- **Content fingerprint**: a reproducible digest over content at a pipeline stage — artifact, extracted entry, or derived object.
- **Source chunk**: a verbatim contiguous excerpt of `JFULL`, carrying the document identity and its offsets. The unit of retrieval and display.
- **Citation candidate** *(derived, new)*: a detected citation-like substring with offsets and provenance, plus a classification state. It is **not** a statute citation and carries no statute identity.
- **Citation** *(user-visible, distinct from candidate)*: the reference shown alongside a displayed passage, leading back to the authoritative artifact.
- **Answer block**: a labelled unit of a response — either quoted source text with citation, or separately-labelled model commentary. Never merged.
- **Golden record**: a fixed judgment retained verbatim as a fixed reference for detecting unintended pipeline change.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of displayed judicial source passages are character-for-character identical to the corresponding source content, verified by automated comparison over a fixed sample of at least 100 documents spanning multiple courts, document types, and years.
- **SC-002**: 100% of source chunks are verbatim substrings of their originating document, verified automatically with no exceptions permitted.
- **SC-003**: 100% of displayed judicial answers expose a complete provenance chain reaching the authoritative source.
- **SC-004**: 0 occurrences of model-generated content stored in or presented as authoritative judicial source text.
- **SC-005**: For a curated set of at least 30 questions whose answers are absent from the corpus, 100% of responses decline without asserting a legal conclusion.
- **SC-006**: Re-ingesting an unchanged source corpus produces 0 duplicate documents and 0 unexpected content changes.
- **SC-007**: Any single-character difference between stored and displayed text is attributable to a recorded technical transformation; 0 unexplained differences.
- **SC-008**: 100% of users shown a legal answer can identify its source document and open the official record, without operator assistance.
- **SC-009**: Running the full pipeline over the golden record set produces byte-identical output across repeated runs, with 0 variance.
- **SC-010**: 100% of repealed and abandoned provisions remain retrievable and are visibly marked as such.
- **SC-011**: 0 regressions in existing statute retrieval, citation, and evaluation behaviour.
- **SC-012**: The constitution records the immutability, non-storage, provenance, and non-alteration principles before any pipeline code implementing them is merged.
- **SC-013**: Every stored `JFULL` is byte-identical to the source, verified by re-hashing extracted entries against the archive's recorded size **and** CRC32; 0 mismatches.
- **SC-014**: Every chunk and every citation candidate satisfies `text == JFULL[start:end]` exactly; 0 exceptions.
- **SC-015**: 0 citation candidates carry a statute identity. Detection emits offsets and text only.
- **SC-016**: The citation detector recognises **both** numeral systems; the Chinese-numeral forms observed in the sample (`第四十四條`, 391 occurrences) are all detected.
- **SC-017**: 0 detection results whose `matched_text` differs from the authoritative substring at the recorded offsets.
- **SC-018**: Re-running the pipeline over an unchanged artifact produces byte-identical derived output; 0 variance.
- **SC-019**: Schema drift is detected rather than absorbed — an entry whose key set differs from the 8-field contract is reported, not silently accepted.

---

## Assumptions

- **The artifact remains available and re-acquirable.** Acquisition is manual. The provider's `/file` API defect is unresolved, so each monthly refresh depends on a fix or another manual download.
- **The Judicial Yuan remains the sole authority.** No secondary or derived source may be substituted without an explicit decision.
- **The observed schema is stable across the corpus.** VERIFIED IN SAMPLE only. Corpus-wide uniformity is UNKNOWN; a future ingestion run MUST detect drift rather than assume stability.
- **Statute and judgment are distinct authorities.** Judgment content originates from the Judicial Yuan; statute content originates from the Ministry of Justice. They are never merged into one apparent authority.
- **The judgment schema is not the statute schema.** Shared tooling MAY be reused; shared *assumptions* may not. Specifically, `ingest/laws/normalize.py::_clean()`'s per-line stripping is forbidden here.
- **Immutability is enforced structurally, not by instruction.** A prompt telling a model not to fabricate is advice; the guarantees rest on stored, verifiable data.
- **Fidelity is defined against the archive bytes.** The artifact is the reproducibility anchor.
- **"Cannot be proven not to alter meaning" is decided conservatively.** Where doubt exists, no transformation is applied. This errs toward ugliness over alteration, deliberately.
- **Golden records come from the artifact**, never hand-authored — a fabricated golden record would itself be an unsourced document.
- **Evaluation measures retrieval and generation separately**, per constitution Principle VI, so a fidelity regression is attributable to the layer that caused it.

### Security and privacy — open, not resolved

**VERIFIED**: `JFULL` contains **unmasked personal names**. Addresses are pre-masked by the source (`○○區○○路000号`). Non-public case content also appears in full.

**FR-002 forbids the pipeline from masking, redacting, or altering names.** This is a deliberate non-action. Consequences that remain **UNKNOWN** and require an explicit decision before UI exposure:

- whether judgment text may be returned verbatim in API responses;
- whether any `JFULL` content may be written to logs;
- whether retrieval results may be indexed or cached.

The ingestion layer MUST preserve names verbatim. Any access control or redaction belongs at a **separate presentation boundary**, never in ingestion.