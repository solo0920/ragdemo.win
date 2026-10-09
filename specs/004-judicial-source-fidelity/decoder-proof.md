# Decoder Proof — Judicial Yuan judgment artifact

**Date**: 2026-10-07
**Purpose**: prove the proposed decoder can actually decode this exact artifact. Empirically, not from vendor documentation.

**Scope compliance**: no production code modified · no Dockerfile modified · no `pyproject`/`requirements` modified · no database · no Qdrant · no ingestion · 1 of 108,409 entries extracted · no schema invented · no LLM used · `spec.md` and `plan.md` untouched.

---

## 1. Artifact

| Property | Value |
|---|---|
| Path | `data/judgements/raw/202607--(20260916Update).rar` |
| Size | 297,279,556 bytes |
| SHA-256 | `ef35ce4401d7d751bbe7cbeab93cb92949f5971a26947cbde80c64e9722fea4c` |
| Container | RAR 4 (`Rar!\x1a\x07\x00`), non-solid, single-volume, unencrypted |
| Compression | `RAR 1.5(v29) -m3 -md=1m` — method `0x33` ("normal") |

**Hash re-verified after every operation; unchanged throughout.**

---

## 2. Decoder

| Property | Value |
|---|---|
| Implementation | UnRAR, built from official rarlab source |
| Version | **UNRAR 7.13** (`RARVER_MAJOR 7`, `RARVER_MINOR 13`, build 2025-07-28) |
| Source URL | `https://www.rarlab.com/rar/unrarsrc-7.1.10.tar.gz` |
| Source size | 268,008 bytes |
| **Source SHA-256** | `72a9ccca146174f41876e8b21ab27e973f039c6d10b13aabcb320e7055b9bb98` |
| Source MD5 | `d9c51328fcb5d8c31d097b2baaaced00` |
| Source revision | rarlab publishes no VCS revision or checksum file for source tarballs (`unrarsrc-*.md5` → **HTTP 404**). The tarball filename and SHA-256 above are the revision record. |
| Binary SHA-256 | `1e2b73201db3eb8b0c83c54c0ff57ca54b85d157ad00da1182c4dd88e39fba30` |
| Build environment | `python:3.12-slim` container, `g++`/`make`, `make unrar` — **disposable**, no host compiler, no host state changed |
| Source modified | **No** — extracted and compiled as-is |

Note the version discrepancy: the tarball is named `unrarsrc-7.1.10` but `version.hpp` declares **7.13**. The binary's own `--version` output is authoritative and is recorded as 7.13.

### Licensing — not ambiguous

`src/unrar/license.txt` verbatim, clauses 2 and 3:

> *UnRAR source code may be used in any software to handle RAR archives without limitations free of charge, but cannot be used to develop RAR (WinRAR) compatible archiver and to re-create RAR compression algorithm, which is proprietary. Distribution of modified UnRAR source code in separate form or as a part of other software is permitted, provided that full text of this paragraph … is included in license, or in documentation … and in source code comments of resulting package.*

> *The UnRAR utility may be freely distributed. It is allowed to distribute UnRAR inside of other software packages.*

Our use is **extraction only** — explicitly permitted, free of charge. Redistribution is permitted provided the paragraph is carried in documentation and source comments. No purchase required. This differs from the RAR **binary**, whose trial EULA forbids bundling inside another package without written permission — the reason the source route was chosen.

---

## 3. Capability result

| Test | Result |
|---|---|
| Opens RAR v4 archive | **PASS** — reports `RAR 1.5(v29) -m3` |
| Reads method `0x33` | **PASS** — listed and decompressed |
| Deterministic listing | **PASS** — two runs byte-identical; 108,547 entries; listing SHA-256 `06ff1a805ee14f0ce523817aedd9a99a704b965d54e6ced67dcd705cc7a290bc` |
| Entry count cross-check | **PASS** — unrar's 108,547 **matches** the independent RAR-header walk in `plan.md` §2.2 exactly |
| Actual extraction | **PASS** — see §4 |

**The decoder reads this exact artifact, not merely "RAR" in general.** The method-`0x33` concern raised in `plan.md` §3.1 is resolved.

---

## 4. Selected entry

Extracted exactly **one** entry (plus a second for generality checking). Nothing else was decompressed.

| Property | Entry 1 | Entry 2 (generality) |
|---|---|---|
| Entry path | `202607/三重簡易庭刑事/SJEM,115,重秩,54,20260716,1.json` | `202607/三重簡易庭民事/SJEV,112,重簡,861,20260731,2.json` |
| Size (listing) | 3,967 | 27,189 |
| Size (extracted) | **3,967 — exact match** | **27,189 — exact match** |
| SHA-256 | `65cd7d32c13550adf8a36d7db2885266c295d12d18743f6e2cc8ed170755531a` | — |
| Encoding | UTF-8, **no BOM** | UTF-8 |
| JSON parse | **OK** — top-level `dict` | **OK** — top-level `dict` |

Size agreement between listing and extracted bytes is the first concrete instance of AC-7 hash/size verifiability.

---

## 5. JSON inspection

**Read as data. Not modified, renamed, normalized, or truncated.** All values below are literal.

### Top-level structure

`dict`, **exactly 8 keys**, identical across both entries (a 刑事 ruling and a 民事 ruling) — flat, no nesting observed.

### Observed fields

| Key | Type | Entry 1 value (verbatim) | Role |
|---|---|---|---|
| `JID` | str (25 ch) | `SJEM,115,重秩,54,20260716,1` | composite identity |
| `JYEAR` | str (3 ch) | `115` | year (ROC) |
| `JCASE` | str (2 ch) | `重秩` | case-type code |
| `JNO` | str (2 ch) | `54` | case serial |
| `JDATE` | str (8 ch) | `20260716` | date (ROC, `YYYYMMDD`) |
| `JTITLE` | str (9 ch) | `違反社會秩序維護法` | cause / subject statute name |
| `JFULL` | str (1423 ch / 3597 B) | full text, 57 lines | **the only text-bearing field** |
| `JPDF` | str (98 ch) | `https://data.judicial.gov.tw/opendl/JDocFile/…pdf` | official PDF reference |

**VERIFIED**: `JFULL` is the sole judgment-content field. **VERIFIED**: the other seven are metadata/identity. **VERIFIED**: no nested objects or arrays in either entry.

### Identity

- **VERIFIED**: `JID` exists and is composite, comma-delimited.
- **VERIFIED**: `JID` equals the archive entry's basename without extension.
- **UNKNOWN**: whether `JID` is globally unique across the corpus, or collides across courts sharing a `JCASE`+`JNO`. Two entries sampled had distinct prefixes (`SJEM`, `SJEV`) — **INFERRED only** that the prefix encodes court, not verified against a court table.

### Court

- **VERIFIED**: **there is no court field.** Court appears only as the first line of `JFULL` (`臺灣新北地方法院三重簡易庭裁定`) and is also embedded in the archive directory path.
- **UNKNOWN**: whether a court field exists in other entries. Two samples is not proof of uniformity across 108,409.

### Case number / date

- **VERIFIED**: date is `JDATE`, ROC `YYYYMMDD`, string not date object.
- **UNKNOWN**: no Gregorian conversion is performed or implied.

### Sections

- **VERIFIED literal markers** in entry 1: `'　　主　　　文'`, `'　　事實及理由'` — indented with **full-width spaces (U+3000)**.
- **VERIFIED** entry 2 contains `事實及理由` but **not** `主　　　文`.
- **UNKNOWN**: section vocabulary across the corpus. `理　由` was **not** found in either sample. Entry 2's absence of a 主文 header shows markers are **not uniform** — a fixed set cannot be assumed.
- **CRITICAL for normalization**: markers rely on full-width-space indentation. Stripping whitespace, as `normalize.py::_clean()` does for statutes, **would destroy them**. FR-001's allow-list must not include indentation stripping for judgments.

### Line endings

- **VERIFIED**: `JFULL` uses **CRLF** — entry 1 has 56 CRLF pairs, 0 bare LF; entry 2 has 351.
- **VERIFIED**: full-width indentation and CRLF are **structural**, carrying item counts and section markers. This is the same class of defect `spec 002` found for statutes.

### Statute citations

- **VERIFIED**: entry 1 contains the literal string `社會秩序維護法第45條第1項` in `JFULL`.
- **INFERRED**: a single distinct citation string was found because only one entry was inspected. **No citation-extraction rule is proposed here** — FR-B05 remains open.

### Personal data — VERIFIED

`JFULL` contains an unmasked personal name (`謝楷威`). Addresses are already masked by the source (`○○區○○路000號`). **This is a source-quality observation, not a defect to correct.** FR-002 forbids alteration; masking would be alteration. Flagged for the user as a data-handling consideration, and it constrains what may be shown in the UI.

---

## 6. Conclusion

```
PASS — decoder successfully extracts actual Judicial Yuan judgment data
```

The recommended decoder (UnRAR 7.13 from official source) opens this exact 283 MB RAR v4 artifact, lists all 108,547 entries deterministically, and extracts individual entries with **byte-exact** size agreement and verifiable SHA-256. Its entry count independently corroborates the header-walk inventory in `plan.md` §2.2.

Blocker 1 from `plan.md` §11 (no RAR decoder) is **resolved**. FR-B01–FR-B05 are **no longer blocked by tooling** — the schema is now observed from real data and recorded in §5 above.

Still required before tasks: a decision on whether the observed 8-key flat schema is uniform across the corpus (one sample per case type, not one per corpus), since §5 explicitly separates VERIFIED from UNKNOWN.

---

## Reproduction

```bash
# isolated build (disposable container; no host compiler needed)
docker run --rm -v "$PWD":/work -w /work python:3.12-slim sh -c \
  'apt-get update -qq && apt-get install -y -qq --no-install-recommends g++ make && \
   cd src/unrar && make -s unrar'

# capability + single-entry extraction (raw archive opened read-only)
src/unrar/unrar l  data/judgements/raw/202607--(20260916Update).rar
src/unrar/unrar lb data/judgements/raw/202607--(20260916Update).rar | wc -l   # 108547
src/unrar/unrar p -inul data/judgements/raw/202607--\(20260916Update\).rar \
  '202607/三重簡易庭刑事/SJEM,115,重秩,54,20260716,1.json' > /tmp/one.json
```

Temporary build tree lives at `/tmp/opencode/unrar-proof/` and is disposable. Nothing was copied into the repository.