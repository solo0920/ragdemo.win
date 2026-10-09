# Corpus Schema Survey — fileSetId 70691

**Date**: 2026-10-07
**Predecessor**: `decoder-proof.md` (PASS)
**Purpose**: determine whether the schema observed from two sample judgments is uniform across the corpus, before any contract is written.

**Scope compliance**: raw artifact unmodified (hash re-verified) · `spec.md` / `plan.md` untouched · no application code · no Dockerfile · no requirements · no database · no Qdrant · no ingestion · **494 of 108,409 entries extracted** (0.46%) · no court regex contract · no citation extraction · no normalization · no name masking · no chunking · no LLM.

---

## 1. Provenance

| Property | Value |
|---|---|
| Archive | `data/judgements/raw/202607--(20260916Update).rar` |
| Archive SHA-256 | `ef35ce4401d7d751bbe7cbeab93cb92949f5971a26947cbde80c64e9722fea4c` |
| Decoder | UnRAR 7.13, built from `https://www.rarlab.com/rar/unrarsrc-7.1.10.tar.gz` |
| Decoder source SHA-256 | `72a9ccca146174f41876e8b21ab27e973f039c6d10b13aabcb320e7055b9bb98` |
| Corpus population | 108,409 JSON entries (`fileSetId=70691`) |
| Corpus inventory SHA-256 | `ee35bbaf27bef959…` (name+size+CRC32 for all entries) |
| **Sample set SHA-256** | **`280b95bbdac0bb933568f55297ea352022457600851847b7ca955db3f0c2d8a3`** |

**Extraction integrity — VERIFIED**: all **494/494** extracted files match the archive's own recorded `Size` **and** `CRC32`. Zero mismatches. This is AC-7 hash/size verification in operation, not in theory.

---

## 2. Sampling methodology

Deterministic, derived from the archive listing only. No randomness, no seed, no analyst choice.

| Stratum | Rule | Purpose |
|---|---|---|
| A. `JCASE` common | 60 most frequent codes, min-size + max-size entry each | case-type coverage, length extremes per type |
| B. `JCASE` rare | 20 least frequent codes | tail coverage |
| C. `JCASE` mid | 20 codes at the frequency median | middle of the distribution |
| D. Size deciles | lowest + highest entry of each of 10 deciles | `JFULL` length range |
| E. Branch | 25 evenly-spaced per branch (民事 / 刑事 / 其他) | civil/criminal/other balance |
| F. Court | every 4th court directory, 3 entries each | court spread |
| G. **Anomaly** | **all 139** five-field-filename entries | known structural class, inspected exhaustively |

Resulting coverage: **494 entries** · 3 branches (民事 191 / 刑事 127 / 其他 176) · **84 courts** · **40 dates** · **170 `JCASE` codes** · size range **318 … 777,963 bytes** · all 139 five-field entries.

Reproduce: `unrar lt` → parse `Name`/`Size`/`CRC32` → apply strata A-G in order → `unrar x @listfile`.

---

## 3. Schema fingerprints

### VERIFIED — schema is uniform

| Property | Result |
|---|---|
| Top-level type | `dict` — **494/494** |
| **Distinct key sets** | **1** |
| Key order | `JID, JYEAR, JCASE, JNO, JDATE, JTITLE, JFULL, JPDF` — **494/494** (order is stable) |
| Key count | 8 — **494/494** |
| Value types | `str` for all 8 keys, **494/494** |
| Null values | **0** |
| Empty-string values | `JPDF` only (139, §6) |

The two-field assumption from `decoder-proof.md` §5 is **confirmed across 494 samples spanning 84 courts, 170 case types and all three branches**. This is the single most important result of the survey.

### Field statistics

| Key | Type | Present | Format observed | Notes |
|---|---|---|---|---|
| `JID` | str | 494/494 | `PREFIX,year,case,no,date,inst` | composite; equals entry basename minus `.json` |
| `JYEAR` | str | 494/494 | 2–3 digits | **ROC year of the case; NOT derivable from `JDATE`** (§7) |
| `JCASE` | str | 494/494 | CJK code, e.g. `重秩`,`訴`,`簡` | 1,336 distinct corpus-wide; 395 occur once |
| `JNO` | str | 494/494 | digits, no padding | serial within case type + year |
| `JDATE` | str | 494/494 | `YYYYMMDD`, ROC | release/batch date; 48 distinct corpus-wide |
| `JTITLE` | str | 494/494 | free CJK text | cause/subject. **Not the court** |
| `JFULL` | str | 494/494 | multi-line CRLF text | **sole judgment-content field** |
| `JPDF` | str | 355/494 non-empty | `https://data.judicial.gov.tw/opendl/JDocFile/*.pdf` | 355/355 match the pattern; 0 exceptions |

---

## 4. `JFULL` structural statistics

### Line endings — VERIFIED

| Pattern | Count |
|---|---|
| **CRLF only** (no bare LF, no bare CR) | **493 / 494** |
| Bare LF present | 0 |
| Bare CR present | 0 |
| No line breaks at all | **1** (§6 outlier) |
| Empty `JFULL` | **0** |

CRLF is uniform and **must be preserved byte-exactly**. This is the same structural class `spec 002` found for statutes.

### Full-width space (U+3000) — VERIFIED

Present in **264 / 494** (53%). Where present it forms section indentation, e.g. `'　　主　　　文'`.

**Consequence**: any whitespace stripping — including the existing `ingest/laws/normalize.py::_clean()` per-line `strip()` — **destroys these markers**. FR-001's allow-list must exclude indentation normalization for judgments.

### Section markers — VERIFIED, and NOT uniform

Markers observed (entry counts):

| Marker | Entries |
|---|---|
| `理由` | 276 |
| `主文` | 220 |
| `理　由` (full-width) | 128 |
| `事實及理由` | 84 |
| `原告之訴` | 56 |
| `主　　　文` (full-width ×3) | 48 |
| `據上論結` | 29 |
| `被告之辯` | 3 |

**46 distinct marker combinations** were observed, including 133 entries with **no recognised marker at all**.

**VERIFIED**: a fixed marker set cannot be assumed. `主　　　文` appears in only 48 of 494; `decoder-proof.md` §5 already recorded entry 2 lacking it. Both observations are now confirmed at scale.

### Court information — INFERRED only, not established

- **VERIFIED**: `JFULL` line 0 contains a court name in **487/494**.
- **VERIFIED**: **7 entries** have no court in line 0. Examples, traceable:

| Entry path | Line 0 (verbatim) |
|---|---|
| `202607/臺灣士林地方法院刑事/SLDM,115,聲,1089,20260724,1.json` | `因本件為對不公開案件聲請停止執行，故本件裁定不公開。` — *case commentary, not a court* |
| `202607/…/PCEV,114,板簡,2533,20260714,1.json` | `宣　　示　判　　決　　筆　　錄` |
| `202607/…/TYDM,115,附民移調,1749,20260723,1.json` | `調  解  筆  錄` |
| `202607/…/TPTA,115,續收,5607,20260728,1.json` | `宣示裁定筆錄` |

- **VERIFIED**: line 0 takes at least 14 distinct suffix shapes (`民事裁定`, `高等裁定`, `支付命令`, `刑事簡易判決`, `分院刑事裁定`, `福建民事裁定`, …). Document type and court are **entangled in one line**.
- **VERIFIED**: 54 entries name a chamber in line 0 whose parent court differs from the archive directory stem (e.g. dir `三重簡易庭刑事` → line 0 `臺灣新北地方法院`). These are **chamber→parent relationships, not contradictions**.
- **VERIFIED**: no entry names a court inconsistent with its archive directory.
- **UNKNOWN**: no court↔directory mapping table exists in the corpus or in this repository. Line 0 is free text with abbreviations.
- **UNKNOWN**: whether the 7 non-court lines have a court recoverable elsewhere.

**Position**: `court` remains **UNKNOWN / INFERRED**. The archive directory is a reliable *path* attribute; line 0 is an unparsed candidate. Neither is yet an authoritative field.

### Statute citations — partially observed

`decoder-proof.md` §5 found `社會秩序維護法第45條第1項` in one sample. **Not surveyed here** — extraction requires a pattern contract, which is out of scope for this step. FR-B05 stays open.

---

## 5. Outliers

**141 / 494** entries deviate from the strict fingerprint. Every one is individually traceable.

| # | Deviation | Count | Classification |
|---|---|---|---|
| 1 | `JPDF == ""` | 139 | **Structural class, not corruption** (§6) |
| 2 | `JFULL` has no line breaks | 1 | Genuine source anomaly |
| 3 | `JYEAR == "79"` | 1 | Genuine source anomaly |

### Non-JPDF outliers, in full

**Outlier A — no line breaks**
```
path : 202607/臺灣士林地方法院刑事/SLDM,115,聲,1089,20260724,1.json
JID  : SLDM,115,聲,1089,20260724,1
text : 因本件為對不公開案件聲請停止執行，故本件裁定不公開。
tag  : size-decile0:low   (smallest entry in the corpus)
```
A non-public case where the entire judgment is one sentence. Legitimate source content.

**Outlier B — year mismatch**
```
path  : 202607/臺灣臺南地方法院民事/TNDV,79,司,900,20260706,19.json
JID   : TNDV,79,司,900,20260706,19
JYEAR : '79'   (ROC 1990)
JDATE : '20260706'  (ROC 115 release batch)
```
Not a parse artifact — `JID` and the filename both say `79`. A 1990 case republished in a 2026 batch.

**Neither may be auto-corrected.** FR-002 forbids alteration. Both must round-trip through the pipeline unchanged.

---

## 6. The 139 five-field entries — 憲法法庭 class

**VERIFIED, exhaustively** (all 139 inspected, not sampled):

| Property | Value |
|---|---|
| Count | 139 |
| Archive directory | exactly one: `憲法法庭憲法` |
| ID prefix | `JCCC` |
| `JCASE` codes | `審裁`, `統裁` |
| Filename form | `JCCC,year,case,no,date.json` — **5 fields, no instance field** |
| `JID` form | `JCCC,115,審裁,1158,20260701` — **5 fields** |
| `JPDF` | **empty in all 139** |
| `JFULL` | present, CRLF, schema otherwise identical |

**VERIFIED correlation**: `JPDF` is empty **if and only if** the entry is a 5-field `JCCC` Constitutional Court ruling. 139/139 and 355/355 — no other case produces an empty `JPDF`.

This is a **document class**, not corruption: Constitutional Court rulings have no per-document PDF on the open-data portal. `JID` lacks the trailing instance field that court rulings carry.

**Contract consequence**: `JID` is composite and **structurally variable** (5 or 6 fields). Any identity contract must key on the whole `JID` string, never on positional parsing of it.

---

## 7. `JYEAR` and `JDATE` are independent — VERIFIED

**80 / 494** entries have `JYEAR ≠ (JDATE's ROC year)`:

```
SJEV,112,重簡,861,20260731,2   JYEAR=112  JDATE=20260731 (batch ROC 115)
CLEV,114,壢簡,2077,20260708,2  JYEAR=114  JDATE=20260708 (batch ROC 115)
```

`JDATE` is the **release/batch date**; `JYEAR` is the **case year**. They are frequently different and neither is derivable from the other.

**Contract consequence**: neither may be recomputed from the other, and neither may be treated as redundant. This is a concrete instance of spec FR-016 — synthesizing a field from another would fabricate judicial metadata.

---

## 8. Source-quality observations (must not be corrected)

- **Unmasked personal names** appear in `JFULL` (confirmed in `decoder-proof.md` §5). Addresses are pre-masked by the source (`○○區○○路000號`). FR-002 forbids the pipeline from masking, redacting, or altering names. This constrains UI display and any logging of `JFULL`, and is flagged for the user as a data-handling decision.
- **Non-public case content** appears in full in Outlier A.
- **Chamber/parent court naming** is inconsistent between archive path and text.

---

## 9. Impact on FR-B01–FR-B05

| Req | Prior state | **New state** | Basis |
|---|---|---|---|
| **FR-B01** source identity | tooling-blocked | **VERIFIED — tooling + evidence resolved** | Key on whole `JID`; 5-field `JCCC` class confirmed; `JID` == entry basename |
| **FR-B02** field mapping | awaiting uniformity | **VERIFIED — 8 keys uniform across 494 samples** | §3; 1 key set, all `str`, stable order, 0 nulls |
| **FR-B03** fidelity | had artifact evidence | **VERIFIED — strengthened** | 494/494 size+CRC32 match; raw hash unchanged |
| **FR-B04** lossless handling | CRLF/whitespace needed | **VERIFIED — contract inputs known** | CRLF 493/494; U+3000 in 264/494; 2 real anomalies must round-trip |
| **FR-B05** citations | awaiting pattern survey | **STILL BLOCKED** | Not surveyed; needs a pattern contract, then empirical validation |

**Net**: 4 of 5 resolved. FR-B05 alone remains open, and it is now the *only* blocker.

---

## 10. Recommended changes to spec and plan

**Not applied.** Listed for approval.

### `spec.md`

1. **§Blocking Finding is now obsolete.** The source is reachable and the artifact is acquired. Replace with a "Resolved" statement referencing `decoder-proof.md`.
2. **§2 Authoritative Source** — record the artifact identity, `fileSetId=70691`, and that acquisition is a **manual, human step** feeding the pipeline.
3. **New requirement FR-028** — `JID` MUST be stored as an opaque composite string; positional parsing of its fields is forbidden (5-field `JCCC` class).
4. **New requirement FR-029** — `JYEAR` and `JDATE` MUST be stored as independent source values; neither may be derived from the other.
5. **New requirement FR-030** — `JPDF` MAY be empty; emptiness MUST NOT be treated as an error, and MUST NOT be filled with a guessed URL.
6. **New requirement FR-031** — section markers are not uniform; any section handling MUST be evidence-driven per document, never a fixed vocabulary.
7. **§4 Cleaning Policy** — add explicitly to the *forbidden* list: whitespace stripping of full-width-space indentation. Reference the `normalize.py::_clean()` divergence.
8. **New requirement FR-032** — unmasked personal names in `JFULL` MUST be preserved verbatim and handled as a data-governance question outside ingestion.

### `plan.md`

9. **§3.1 blocker resolved** — decoder proven; `rarfile`+UnRAR-from-source strategy validated end to end.
10. **§5 architecture** — insert the verified 8-key schema as the adapter's input contract; keep the adapter tolerant of the 5-field class.
11. **New task T-02a** — corpus schema regression fixture: pin the 494-entry sample SHA-256 as a fixture manifest so future schema drift is detectable.
12. **T-09 (schema discovery) is now complete** — its output is §3 of this survey.
13. **§9 data safety** — add the Constitutional Court class to the deletion-semantics discussion; 139 documents have no PDF and a different id shape.

---

## 11. Confidence summary

| Claim | Status |
|---|---|
| 8-key flat schema uniform across 84 courts / 170 case types / 3 branches | **VERIFIED** (n=494) |
| All values `str`; no nulls; key order stable | **VERIFIED** |
| `JFULL` is the sole content field | **VERIFIED** |
| CRLF is the uniform line ending | **VERIFIED** (493/494) |
| U+3000 indentation is structural, not cosmetic | **VERIFIED** |
| Section markers are not uniform (46 combos) | **VERIFIED** |
| `JPDF` empty ⟺ 憲法法庭 5-field class | **VERIFIED** (139/139, 355/355) |
| `JYEAR` ≠ `JDATE` year in 80/494 | **VERIFIED** |
| Extraction integrity | **VERIFIED** (494/494 size+CRC32) |
| Court recoverable from `JFULL` line 0 | **INFERRED** — 487/494; 7 exceptions incl. non-court text |
| Court↔directory mapping table | **UNKNOWN** — does not exist |
| Court name normalization across 137 dirs / 14+ line-0 shapes | **UNKNOWN** |
| `JCASE` semantics (1,336 codes, 395 singletons) | **UNKNOWN** |
| Statute citation patterns | **UNKNOWN** — FR-B05 unsurveyed |
| Corpus-wide uniformity beyond the 494 sample | **UNKNOWN** — 0.46% sample |

---

## Conclusion

Corpus schema uniformity is **established for the 8-key flat structure** across a deterministic 494-entry sample spanning every major stratum of the corpus. The two assumptions that would have broken the data model — a fixed section-marker vocabulary, and a fixed `JID` field count — are both **disproved by evidence**.

`court` remains **UNKNOWN/INFERRED** and must not enter a contract yet.

The blocker has narrowed from five to one: **FR-B05, statute-citation patterns**.

Not yet sufficient for `/speckit.tasks`: FR-B05 needs a pattern contract before it can be validated, and the corpus-wide claim needs a decision on whether 0.46% sampling is acceptable or a second, larger pass is required.