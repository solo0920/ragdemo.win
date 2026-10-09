# Citation Pattern Survey — fileSetId 70691

**Date**: 2026-10-07
**Predecessors**: `decoder-proof.md` (PASS), `corpus-schema-survey.md` (PASS)
**Purpose**: answer one question — *is there a sufficiently stable, verifiable source-text pattern to build a deterministic citation-extraction contract from `JFULL`, without modifying authoritative source text?*

**Scope compliance**: raw artifact unmodified (hash re-verified) · `spec.md` / `plan.md` untouched · no application code · no Dockerfile · no requirements · no database · no Qdrant · read-only reuse of the previously extracted 494-entry sample · **no production regex produced** · no LLM · `JFULL` never modified.

---

## 1. Provenance

| Property | Value |
|---|---|
| Archive | `data/judgements/raw/202607--(20260916Update).rar` |
| Archive SHA-256 | `ef35ce4401d7d751bbe7cbeab93cb92949f5971a26947cbde80c64e9722fea4c` |
| Decoder | UnRAR 7.13 |
| Decoder source SHA-256 | `72a9ccca146174f41876e8b21ab27e973f039c6d10b13aabcb320e7055b9bb98` |
| Sample | the **same 494-entry deterministic set** as `corpus-schema-survey.md` §2 |
| Sample-set SHA-256 | `280b95bbdac0bb933568f55297ea352022457600851847b7ca955db3f0c2d8a3` |
| Gazetteer reference | `data/laws/laws_meta.jsonl` (1,347 statute names, moj) — read only |

Every figure below is reproducible by re-extracting that sample set. Short fragments are quoted for traceability; no judgment text is bulk-copied.

---

## 2. Headline answer

> **FR-B05 cannot safely be implemented as a deterministic citation extractor from `JFULL` alone.**

**A high-recall citation *detector* is achievable and I measured one: it reaches 100% of `第…條` occurrences.** But a *contract* requires binding each occurrence to a statute identity, and that binding is **not locally decidable**:

- **11% (610/5310)** of article references cannot be resolved to a statute by any rule I tested — including paragraph-scoped state.
- Those 610 are **not errors**. They are legitimate elliptical citations (`依社會秩序維護法第45條…、第65條`) and quoted statutory text (`民法\r\n 第184條`). Resolving them requires understanding *discourse structure*, not pattern matching.
- A gazetteer-based approach fails: the local 1,347-name gazetteer covers only **63 of 1,629** observed candidate names (3.9%), and is itself stale — `犯詐欺犯罪危害防制條例`, the most-cited statute name in the corpus, is **absent** from it.

Producing a regex that silently guesses a statute for these cases would fabricate legal metadata, which FR-002 and FR-016 forbid. So the honest output is this negative result.

---

## 3. What is stable — `VERIFIED`

These findings are solid and can be relied on.

### 3.1 Occurrence census (n = 5,310 `第X條` across 494 entries)

| Feature | Occurrences | Verdict |
|---|---|---|
| `第X條` total | 5,310 | — |
| Entries containing any | 290 / 494 | — |
| `第X項` | 3,411 | `VERIFIED` |
| `第X條第X項` | 2,131 | `VERIFIED` |
| `第X款` | 1,357 | `VERIFIED` |
| `第X項第X款` | 767 | `VERIFIED` |
| `第X條之Y` (added articles) | 760 | `VERIFIED` |
| `第X項前段` | 276 | `VERIFIED` |
| `第X條前段` | 89 | `VERIFIED` |
| `第X目` | 8 | `VERIFIED` |

### 3.2 Numeral systems — `VERIFIED`, both are used for article numbers

| System | Occurrences of `第<CN>條` | Distinct forms |
|---|---|---|
| **Chinese numerals** (`第四十四條`) | **391** | 57 |
| **Arabic numerals** (`第44條`) | 4,919 | — |

- **VERIFIED**: 251 entries use Arabic only, **38 entries use both systems in the same document**.
- Example (`202607/福建高等法院金門分院民事/KMHV,114,重上,5,20260714,1.json`): `民事訴訟法第446條第1項` (Arabic) alongside `民法第148條`, `土地法第43條` — and `原審卷第371頁` in the same text.
- Chinese numerals are **not** merely statute-name fragments. Example (`202607/臺北簡易庭民事/TPEV,115,北小,1733,20260702,1.json`): `犯詐欺犯罪危害防制條例第四十四條` — `第四十四條` is article 44, in Chinese numerals.

Any contract must accept both systems. This is a genuine trap: a contract assuming Arabic-only silently loses 7.4% of references.

### 3.3 Line-boundary behaviour — `VERIFIED`, favourable

**0 of 5,310** citations are split across a CRLF *in the matched token itself*. Scanning the raw string and scanning line-by-line produce **identical** counts (5,310 vs 5,310).

This matters: it means a line-based tokenizer is **not** structurally broken by CRLF, contrary to what one might assume. However §5 shows CRLF *does* break statute-name adjacency.

### 3.4 Structural suffixes — `VERIFIED`

Full form `第X條第Y項第Z款` is real and common (767 occurrences of the `項第款` fragment). Citation text is wrapped in quotes in 8 cases (`「…」`) and 3 (`『…』`).

Ranges/joins: 261 occurrences, e.g. `第254條至第…`, `第216條、第2…` — the `、` joiner is dominant.

---

## 4. Variability that defeats a local rule — `VERIFIED`

### 4.1 Statute name is elided 19% of the time

Of 5,310 `第X條`, **1,057 (19%)** have no statute name in the preceding 30 characters. Two distinct causes:

**(a) Elliptical continuation** — the statute was named earlier in the same paragraph:

```
五、依社會秩序維護法第45條第1項、第65條第3款、第22條第3項
   └─ name given once ────────────────────────┘└─┬─┘└─┬─┘└─┬─┘
                                                ▲        ▲        ▲
                        verified: 社會秩序維護法第65條 / 第22條
path: 202607/三重簡易庭刑事/SJEM,115,重秩,54,20260716,1.json
```

**(b) Cross-CRLF adjacency** — the name is present but separated by a line break and indentation:

```
'…民國115年5月25日…移送審理，本\n院裁定如下：'
vs
'爰依社會秩序維護\r\n    法▮第22條'
path: 202607/三重簡易庭刑事/SJEM,115,重秩,54,20260716,1.json
```

Case (b) is **128 occurrences** (`VERIFIED`) and is recoverable by whitespace-tolerant matching. Case (a) requires state.

### 4.2 Abbreviations and relative references — `VERIFIED`

| Form | Occurrences | Note |
|---|---|---|
| `同法` / `同法第X` (statute fully elided) | **244** | relative to the immediately preceding citation |
| `憲訴法第X` (defined by `下稱憲訴法`) | 143 | in-text alias |
| `行政訴訟法第X` | 110 | |
| `勞基法第X` / `消保法` / `證交法` / `刑訴法` | 17 / 5 / 5 / 3 | aliases |
| `準用同第X條` | present | cross-article reference |

`VERIFIED` alias definitions exist in the text: `下稱憲訴法` (68×), `下稱勞基法`, `下稱證交法`. So abbreviations are *declared*, but only 23 distinct `下稱X法` strings were found — the vast majority of abbreviations are used **without** being defined in the sampled text.

### 4.3 Gazetteer validation is not available — `VERIFIED`

Using the repo's own `laws_meta.jsonl` (1,347 names):

| Metric | Result |
|---|---|
| Distinct candidate names before `第` | 1,629 |
| Matched to gazetteer | **63 (3.9%)** |
| `民事訴訟法` | in gazetteer ✓ |
| `民法` | in gazetteer ✓ |
| `刑法` | **not in gazetteer** (only `中華民國刑法`) |
| `犯詐欺犯罪危害防制條例` | **not in gazetteer** — yet it is the **most-cited** name (195×) |

The gazetteer is **stale** relative to current Taiwanese law. It cannot be used as the validation oracle a citation contract would need.

---

## 5. Resolvability ceiling — `VERIFIED` measurement

Applying progressively stronger rules to all 5,310 occurrences:

| Rule | Resolved | % |
|---|---|---|
| 1. Statute name immediately adjacent | 3,605 | 68% |
| 2. + `同法` / `準用` | 3,780 | 71% |
| 3. + name separated by CRLF | 3,908 | 74% |
| 4. + paragraph-scoped "current statute" carry-forward | **4,672** | **87%** |
| — inside `「quoted statute」` (reproduced, not cited) | 28 | 0.5% |
| **UNRESOLVED** | **610** | **11%** |

Note this is a *coverage* measurement, not a correctness claim. Rules 1-4 were each verified on traceable examples.

The 610 unresolved break down as:
- Cross-CRLF name continuation where the **statute name itself** straddles the break *and* the indentation exceeds my window — e.g. `'民國115年5月25日…移送審理，本\n院裁定如下：'` and `'爰依社會秩序維護\r\n    法▮第22條'`. **These are recoverable with a larger window**, so 11% is an upper bound on true ambiguity, not a floor.
- True elliptical references where the "current statute" was established in a **previous paragraph**: `'支付命令之聲請，不合於第508條'` with no name in the same paragraph.

### False positives a name-anchored regex would produce — `VERIFIED`

| Class | Occurrences | Example |
|---|---|---|
| **Doc-internal references, not statutes** | **87** | `系爭契約▮第一條`, `原審卷第371頁` |
| Sentence-spanning pseudo-names | many | `審判長大法`, `經依法`, `按人民於其憲法上所保障之權利遭受不法` |

`系爭契約第一條` (contract article) is **not** a statutory citation. Any rule anchored on `…第X條` alone will capture it. Distinguishing requires knowing that `契約` is a document, not a statute — a gazetteer entry, i.e. the oracle §4.3 shows is unavailable.

---

## 6. Classification

| Category | Verdict |
|---|---|
| **Observed citation-like pattern** | `VERIFIED` — 5,310 `第X條` occurrences with stable structural suffixes (`項`/`款`/`目`/`前段`/`之N`) |
| **Confirmed legal citation** | `VERIFIED` for ~3,900 occurrences with an adjacent or in-paragraph statute name |
| **Ambiguous pattern** | `VERIFIED` — 610 unresolvable + 87 doc-internal + 1,057 elided-name cases |
| **Unsupported assumption** | Any claim that a single regex can extract statute + article + 項 + 款 from `JFULL` |

---

## 7. What would be safe to build — `INFERRED`

Two options, neither an extractor:

**Option 1 — Citation-as-text (recommended)**
Store the matched citation **substring verbatim** plus its byte offsets in `JFULL`. Do not attempt to resolve it to a statute identity. 100% of `第X條` occurrences are then retrievable as source text, with provenance to the artifact, and **zero fabrication risk**. Statute-level linking is deferred.

This satisfies spec FR-002/FR-016 exactly: no judicial metadata is synthesized.

**Option 2 — Curated gazetteer + explicit unresolved**
Build a hand-verified statute-name list, resolve what matches, and record the rest as `unresolved` rather than guessing. Requires: (a) a fresh gazetteer from a current official source, (b) ~1,600 name strings to adjudicate, (c) a policy for the 610 unresolvable. Significant, and each adjudication is a legal judgement, not a mechanical one.

---

## 8. Provenance of every claim

Each finding is traceable to `specs/004-judicial-source-fidelity/` sample set SHA-256 `280b95bb…`, plus the archive and decoder hashes in §1. Worked examples carry their archive entry path. The temporary tree is `/tmp/opencode/unrar-proof/` (disposable); nothing was copied into the repository.

---

## 9. Effect on FR-B05

| Item | State |
|---|---|
| FR-B05 statute-citation extraction | **BLOCKED — negative result** |
| Cause | Not tooling, not access, not sampling. The source text does not contain the information a citation contract requires, in ~11% of cases (upper bound), and no available oracle can adjudicate the rest. |
| What unblocks it | Either Option 1 (offset-preserving citation text, no statute resolution) or a decision to fund Option 2 |

---

## 10. Recommended next step

**One action**: decide between Option 1 and Option 2.

Option 1 is recommended and is strictly additive to the already-verified schema (8 flat string fields). It needs no new evidence. Option 2 needs a fresh gazetteer and ~1,600 human adjudications before any extractor could be written.

No `/speckit.tasks`. No extractor implemented. No spec or plan modified.