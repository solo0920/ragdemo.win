# Specification Quality Checklist: Judicial Source Fidelity

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-10-07
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

- Items marked incomplete require spec updates before `/speckit.clarify` or `/speckit.plan`

### Validation evidence (2026-10-07)

| Item | Evidence |
|---|---|
| No placeholders remaining | `grep -cE '\[FEATURE NAME\]\|\[DATE\]\|\[ARGUMENTS\]\|\[Brief Title\]' spec.md` → 0 |
| No clarification markers | `grep -c 'NEEDS CLARIFICATION' spec.md` → 0 |
| Section order matches template | Resolved template headings (User Scenarios & Testing → Requirements → Functional Requirements → Key Entities → Success Criteria → Assumptions) appear in the same order in `spec.md` |
| Requirements testable | 27 functional requirements (FR-001…FR-027) with MUST-level normative wording; each is verifiable by inspection of stored vs. displayed bytes, or by a query whose expected refusal is known in advance |
| Requirements cover the request | Request §3 (prohibitions) → FR-001…FR-005; §4 (cleaning policy) → FR-001/FR-003; §5 (raw preservation) → FR-006/FR-007; §6 (integrity) → FR-007…FR-010; §7 (chunking) → FR-011…FR-013; §8 (metadata) → FR-014…FR-016; §10 (answer policy A–D) → FR-018…FR-022; §12 (provenance) → FR-014…FR-017; §15 (AC-1…AC-7) → SC-001…SC-007; §17 (regression protection) → FR-026/FR-027 |
| Success criteria measurable | 12 criteria; 9 state explicit percentages or counts; SC-006/007/009/011/012 are pass/fail gates with stated 100% or 0-variance conditions |
| Technology-agnostic criteria | Criteria are stated as source-verification outcomes ("character-for-character identical to the corresponding source content"), not as tool behaviour. Existing repository identifiers (PostgreSQL table names, Qdrant payload fields, file paths) appear only in the Blocking Finding and Key Entities as *evidence of current state*, never as requirements on the solution |
| Edge cases identified | 9 cases: erroneous official text, oversized judgment, case-number collision across courts, article appearing in two contexts, unused source field, revised source document, source unavailable at ingest, unprovable transformation, newline-as-structure (carried from spec 002) |
| Scope clearly bounded | §Blocked Requirements separates the 5 requirements that cannot be finalized without source access from the 27 that can; "Out of scope" is expressed via FR-023 (existing statute pipeline untouched) and the Explicit Gaps section |

### Deliberate judgement calls

1. **The `/file` endpoint failure is recorded as a Blocking Finding rather than a `[NEEDS CLARIFICATION]` marker.** Three probes establish the failure is real, reproducible, and specific to the `/file` path (`id=1`, `id=2`, `id=100`, `id=70691` all return the same 500). A clarification marker would ask the user to supply the field structure that the source was supposed to supply; instead the spec states what is blocked and why, and separates implementable requirements from blocked ones.
2. **Five requirements are marked `FR-B01`…`FR-B05` [BLOCKED] rather than dropped.** They are real requirements of record. Silently omitting them would let a plan appear complete while omitting the judgment data model entirely.
3. **Repository-specific names appear in the spec, against the technology-agnostic guideline.** Justified: the Blocking Finding is *evidence about the current state* — that no judgment table, collection, or payload source exists — and a spec cannot establish a baseline without describing it. No requirement constrains the solution to use them.
4. **No `[NEEDS CLARIFICATION]` markers raised.** The one genuinely blocking unknown (source field structure) cannot be resolved by asking the user, who would be guessing at Judicial Yuan internals. The remaining gaps had defensible defaults recorded in Assumptions.

### Blocked before planning

The 5 `FR-B*` requirements, and SC-001/SC-002/SC-006/SC-009 in their quantitative form, require a reachable authoritative source. `/speckit.plan` can proceed for the non-source-dependent work (fidelity invariants, raw preservation, hash chain, provenance metadata, constitution rule) but must not design the judgment data model against assumed fields. Resolving source access is a prerequisite for the remaining scope.