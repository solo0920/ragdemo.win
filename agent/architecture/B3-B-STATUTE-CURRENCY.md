# B3-B — Statute Currency & Temporal Validity

Status: **implemented + gated — mechanism correct, operating point empty.**
Terminal: **HOLD → STOP** (B3-C not started).

## 1. Motivation

A judgment citing Article X does not prove today's corpus text is the wording
in force on the judgment date. B3-B makes that distinction mechanical: no
silent substitution of current text for historical text, ever.

## 2. Available temporal metadata (capability audit, read-only)

- Statute rows: NO intervals, NO versions, NO article dates. Only current
  text + is_abandoned/is_repealed flags (current-state, not history).
- laws_meta: law-level modified_date (1347/1347), effective_date (90/1347),
  free-text histories (1347/1347, deliberately unparsed — fragile).
- File-level sync record (sha + 2026/9/24 update + applied_at).
- Judgments: JDATE (8-digit Gregorian, validated strictly); corpus records
  carry no dates; JID never used as a date source (FR-028 + brief §9).

## 3. Temporal contract

Record per citation: judgement/date, citation/law/article, version id +
interval (always null on real data), status (5-state), validity axis,
text axis, provenance (sources, law-level dates, rule id).

## 4–5. Validity & text semantics (kept separate)

VALIDITY (in force?) × TEXT (exact wording available?) tracked on independent
axes. VERIFIED requires both; validity-without-text stays NOT_AVAILABLE;
CURRENT_ONLY carries UNKNOWN validity + CURRENT_TEXT_ONLY.

## 6. Rules T1–T8 (gate names double as rule ids)

T1 exact closed interval (+exact text) → VERIFIED; T2 open-ended → always
UNKNOWN (continuity unprovable in this record shape); T3 outside → INVALID;
T-boundary equality → UNKNOWN (no retroactivity invented, never
INVALID-by-equality); T4 no history → CURRENT_ONLY; T-date-missing/no-record/
malformed-interval → UNKNOWN. Law-level dates recorded, never upgrading
article status (§11 — demonstrated live: 民法 amended 20260817, AFTER the
20260713 demo judgment, stays CURRENT_ONLY).

## 7. Reviewed temporal set

5 real cases (B2-B citations × real/absent/malformed JDATEs): 2 CURRENT_ONLY,
3 UNKNOWN. HISTORICAL_VERIFIED/INVALID exist ONLY in unit tests with
clearly-labeled synthetic intervals (§17 honored — no fabricated gold).

## 8–10. Metrics, false claims, invalid handling

Reviewed statuses 5/5 match on recompute; false historical claims 0;
INVALID versions excluded from answers with a fixed note (tested); gate
T1–T8 PASS on well-formed records and correctly flags malformed input.

## 11. B2-D integration

Opt-in temporal map → status-derived fixed limitation sentences
(CURRENT/UNKNOWN/VERIFIED) + INVALID exclusion, all gate-allowlisted;
default-off preserves legacy output (B2-D/B2-E suites pin it); serve flag
`enforce_temporal` builds the map from payload JDATEs (absent live).
No claim-type changes (separation by non-existence + gate vocabulary).

## 12. B3-A interaction

Attribution untouched; temporal validation consumes RESOLVED citations
regardless of voice layer (citation → statute → temporal, as specified).

## 13–16. Regression / limitations / follow-ups / terminal

- Full suite + validators green → see validation report.
- L1 no article history in corpus (286 single-promulgation laws sized as the
  only future VERIFIED pool, pending a structured source — free-text parsing
  analyzed and declined).
- L2 corpus records lack JDATE (payloads have it; serving must thread it).
- L3 UNRESOLVED_STATUTE E2E still absent (carried).
- Follow-ups → B3-B-FOLLOWUPS.md. Terminal: HOLD → STOP.
