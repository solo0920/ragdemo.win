# Specification Quality Checklist: 判決＋法條 evidence 快速回傳（證據先行）

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-10-10 · **Updated**: 2026-10-10（after /speckit.clarify）
**Feature**: [spec.md](../spec.md)
**Clarify report**: [clarify-report.md](clarify-report.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

**Documented deviation**: "No implementation details" — the maintainer supplied FR-001…FR-011
and SC-001…SC-009, and constitution XII requires measurable, denominated figures. Both together
make "p95 ≤ 20 ms" and "WHERE (pcode, article_seq) IN (...)" unavoidable. Maintainer-directed
exception, not oversight. User Scenarios and Edge Cases remain implementation-free.

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain — **verified: 0** after /speckit.clarify
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [ ] Success criteria are technology-agnostic (no implementation details)
      — documented deviation (constitution XII R1 + maintainer-supplied SCs)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification
      — same documented deviation as above

## Additional Checks (constitution XII)

- [x] Baseline measured **before** any change, archived raw output
      (`evidence/baseline-20261010-192445.txt`, `evidence/rss-probe-20261010-193429.txt`)
- [x] Re-runnable measurement command recorded (`verify/measure-baseline.py`)
- [x] Every cited number states its denominator
- [x] Verified facts separated from inferences
- [x] Superseded figures marked superseded rather than silently replaced

### Clarifications resolved (/speckit.clarify, 2026-10-10)

| Q | Topic | Resolution |
|---|---|---|
| Q1 | Judgment text source at query time | **Option A** — pg `judgment` stores full text, sha256 verified in pg. Capacity recalc deferred to plan with real parquet size |
| Q2 | `not_applicable` scope | **Option B** — only 12 administrative/special-jurisdiction volumes; reason code `admin_special_jurisdiction`; 68 criminal volumes keep normal extraction, validated by new SC-009 |

### Corrections applied per maintainer instruction

1. **前置 task section added** — PRE-1 (`judgements` collection absent), PRE-2 (payload has no
   `text`), PRE-3 (pg `judgment` table absent). All backed by measurement, not inference.
2. **Known-problems section rewritten** — original figures explicitly marked 已作廢, replaced
   with measured values and denominators; a third row added for the container-dependency claim.
3. **SC-003 rewritten** — original ("RSS 相對基線下降") targeted `jfull_map`'s +1 MB increment
   and had no falsifiable threshold. Replaced with: peak RSS ≤ 100 MB **plus** static grep
   **plus** runtime trace. Basis measured: corpus load costs 496 MB (525→29 MB without).

## Notes

- All checklist items pass except the one documented, maintainer-directed deviation
  (technology-agnostic Success Criteria).
- **Plan must begin with PRE-1** (`judgements` collection missing) or FR-002 / SC-008 fail.
- Ready for `/speckit.plan`.