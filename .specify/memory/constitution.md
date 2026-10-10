<!--
SYNC IMPACT REPORT
Version change: 1.2.0 → 1.2.1
Bump rationale: PATCH — wording/cross-reference corrections only. No principle content
  added, removed, or redefined.
Modified principles: none (only two cross-references inside existing sections)
Changed lines (text only):
  - Development Workflow → Constitution Check (gate): "Principles I–X" → "Principles I–XII"
  - Governance → Conflict resolution: NON-NEGOTIABLE list "(I, III, VI, VII, X)"
    → "(I, III, VI, VII, X, XI, XII)"
    Rationale: XI and XII are already titled NON-NEGOTIABLE in their own headings
    (line 179, line 206); the governance list omitted them, which understated their
    precedence. This corrects a pre-existing inconsistency — it was already wrong when
    XI was added without updating the list.
Added sections: none
Removed sections: none
Templates requiring updates:
  ✅ none — verified by grep that .specify/templates/*.md do not enumerate principle
    names or numbers; they read this file at runtime.
Follow-up TODOs: none. This PATCH resolves both deferrals recorded in the 1.2.0 report:
  (1) the stale "Principles I–X" gate reference, and
  (2) the omitted XI/XII in the NON-NEGOTIABLE precedence list.
  The deliberate omission of b1_serve / S1-3 from Principle XII remains a recorded
  decision, not a deferral.
-->

# ragdemo.win Constitution

ragdemo.win is a modular financial-compliance AI platform: legal and regulatory document
ingestion, indexing and retrieval, RAG question answering, evaluation, model/embedding
integration, rule and policy management, and frontend/backend integration.

It is optimized for a **stable core with replaceable components, delivered as incremental
vertical slices** — not for one-off demos.

## Core Principles

### I. Vertical Slice First (NON-NEGOTIABLE)

Every feature MUST deliver a usable, end-to-end vertical slice whenever reasonably possible,
crossing only the minimum layers required (API → service → domain logic → infrastructure →
database / vector DB / external system).

- Work MUST follow: small working slice → verify → extract stable abstraction → extend.
- Features MUST NOT be decomposed into large amounts of unfinished infrastructure because
  future features "may need it".
- Speculative infrastructure with no concrete current feature requiring it MUST NOT be built.

**Rationale**: Working, verified increments expose real problems early and keep the core honest.

### II. Stable Core, Replaceable Components

The architecture MUST distinguish stable platform capabilities (ingestion, retrieval,
querying, evaluation, model selection, document/rule management, API contracts) from
replaceable implementations (LLM providers, embedding models, rerankers, vector databases,
storage, frontend components, external AI services).

- Provider-specific details MUST NOT leak into core application logic.
- When an adapter is needed, it MUST isolate provider behavior behind a small, explicit interface.
- An abstraction MUST NOT be introduced merely because multiple implementations *might* exist.
  Introduce it when a second implementation or a testing need actually exists.

**Rationale**: Models and vector stores change quickly; the platform must not.

### III. Simplicity Over Speculation (NON-NEGOTIABLE)

The simplest architecture that satisfies the current requirement MUST be chosen.

The following MUST NOT be introduced without a concrete current use case: microservices,
message queues, caching layers, extra abstraction layers, generic frameworks, plugin systems,
speculative configuration systems, or duplicate representations of one domain concept.

When two designs satisfy the requirement equally, choose the one with fewer moving parts,
dependencies, and abstractions, and easier local development, testing, and debugging.

**Rationale**: "Future extensibility" is not a justification; every added part is a permanent cost.

### IV. Contract Before Implementation

Public boundaries MUST be explicit: HTTP APIs, request/response schemas, database
boundaries, vector-search interfaces, model/adapter interfaces, and frontend/backend contracts.

- Feature work SHOULD proceed: Requirement → Contract → Test → Implementation → Integration.
- Implementations MUST conform to the contract.
- Changing an existing contract is a deliberate architectural change and MUST NOT happen
  implicitly inside an unrelated feature.
- Backward compatibility SHOULD be preserved unless the spec explicitly permits a breaking change.

**Rationale**: Explicit contracts let frontend, backend, and adapters evolve independently.

### V. Test Behavior, Not Implementation

Tests MUST primarily validate observable behavior and contracts, scaled to risk:

- **Unit**: deterministic domain logic, parsers, transformations, validation, ranking/scoring, configuration.
- **Integration**: FastAPI endpoints, PostgreSQL, Qdrant, ingestion pipelines, model adapters, cross-layer behavior.
- **End-to-end**: only where it gives meaningful confidence in a complete user flow.

Tests MUST NOT exist merely to raise a coverage number. A feature whose important behavior
cannot be reliably verified is incomplete.

**Rationale**: Behavior-level tests survive refactoring and catch real regressions.

### VI. RAG Correctness and Traceability (NON-NEGOTIABLE)

For legal and compliance functionality, correctness and traceability take priority over
answer fluency.

- Any user-facing answer derived from regulatory or legal knowledge MUST preserve enough
  provenance to determine: the source document, the retrieved chunk/passage, the
  retrieval/ranking mechanism used, and the generating model (where applicable).
- The system MUST NOT fabricate citations or imply a source supports a statement it does not.
- When context is insufficient, the system SHOULD say so rather than produce an unsupported answer.
- RAG failures MUST be distinguishable as: retrieval failure, generation failure, or
  source-quality problem.
- Evaluation SHOULD measure retrieval and generation separately when practical.

**Rationale**: In a financial/legal setting, an unsupported but fluent answer is worse than no answer.

### VII. Security and Data Boundaries (NON-NEGOTIABLE)

- Secrets (API keys, tokens, OAuth credentials, database/Qdrant/Cloudflare/model-provider
  credentials) MUST NOT be committed to source control and MUST use the project's
  established secret-management mechanism.
- Sensitive configuration and sensitive data MUST NOT be logged.
- Databases, vector databases, external APIs, and cloud services MUST be accessed with
  least privilege.
- Authentication and authorization boundaries MUST NOT be bypassed to simplify development.

**Rationale**: Compliance data and provider credentials are high-value; leaks are not recoverable.

### VIII. Reproducible Development Environment

The project MUST be reproducible across supported environments: **x570**, **MBP**, and
**MSI / WSL**.

- Python dependencies MUST be managed with `uv`.
- Docker Compose SHOULD be used for coordinated infrastructure (API, PostgreSQL, Qdrant,
  LLM/model service, other required services).
- Local development MUST NOT depend on undocumented manual state; setup MUST be captured in
  version-controlled configuration and documented commands.
- A feature requiring a new dependency or infrastructure service MUST have its plan state:
  (1) why it is required, (2) where it runs, (3) how it is started, (4) how it is tested,
  (5) how it affects existing environments.

**Rationale**: Three machines and WSL make "works on my machine" a constant risk.

### IX. Observability and Debuggability

Important behavior MUST be observable. Errors SHOULD carry enough context to diagnose
failures without exposing secrets or sensitive data.

- Key pipelines SHOULD use explicit stages:
  `ingest → parse → chunk → embed → index → retrieve → rerank → generate → evaluate`.
- Failures SHOULD identify the failed stage rather than collapse into a generic error.
- Debugging SHOULD favor inspectable state and deterministic reproduction over hidden magic.

**Rationale**: RAG failures are hard to localize without stage-level visibility.

### X. AI-Agent Discipline (NON-NEGOTIABLE)

AI coding agents MUST stay within the requested feature scope. An agent MUST NOT:
redesign unrelated architecture, refactor unrelated modules, rename APIs without a
requirement, add speculative features, introduce new services without justification,
rewrite working code for stylistic preference, modify unrelated configuration, expand a
feature because it "would be better", or silently change project conventions.

When implementation reveals a genuinely necessary issue outside the current scope, the agent
MUST: **STOP → explain the issue → propose the smallest required change → obtain approval**
before expanding scope. A small, complete implementation is preferred over a broad,
partially finished redesign.

**Rationale**: Scope creep by automated agents is the fastest way to destabilize the core.

### XI. Judicial Source Immutability (NON-NEGOTIABLE)

For any source text published by a financial or legal authority — including but not limited to
statutes, regulations, and court judgments — the system MUST preserve the authoritative text
byte-for-byte from acquisition through retrieval.

- **Authoritative source text MUST NOT be altered, rewritten, summarized, paraphrased,
  cleaned, or stripped during ingestion, indexing, or retrieval.**
  Cosmetic changes (BOM removal, transcoding to UTF-8) are permitted only when they are
  deterministic, recorded, and reversible, and they MUST NOT change the visible content or
  character offsets.
- **Model output is not authoritative judicial or regulatory content.** Any text generated by
  an LLM MUST be visually and structurally separated from the source text. Model output MUST
  NOT be stored or displayed as though it originated from the authority.
- **Answers derived from source text MUST retain provenance** sufficient to reconstruct:
  the artifact, the document, the field, and the byte offsets of every quoted passage.
- **Retrieval MUST NOT alter source content.** Retrieved chunks MUST be verbatim substrings
  of the authoritative field, and the system MUST be able to prove that relationship by
  re-slicing the stored source.
- **Cross-reference**: This principle specializes Principle VI (RAG Correctness and
  Traceability) for the narrower, stricter case of authoritative financial/legal source text.
  Where Principle VI and Principle XI conflict on source-text handling, Principle XI governs.

**Rationale**: In financial and legal contexts, a fluent but unverifiable answer is worse than
no answer. Immutability is what makes traceability and refusal meaningful; traceability over
mutable text proves nothing, and refusal based on fabricated source text is itself fabrication.

### XII. Measured, Reproducible, and Honest Reporting (NON-NEGOTIABLE)

Any quantitative claim — elapsed time, count, percentage, ratio, recall, coverage — is
governance-bearing and MUST be reproducible or MUST NOT be asserted.

Every such number MUST carry three things together:

1. **The exact command that produces it**, re-runnable as written.
2. **Its raw output archived** under `specs/<feature>/evidence/`.
3. **The denominator stated explicitly**: what the population is and how large it is.

A number missing any of the three MUST NOT appear in a spec, a commit message, or a report.
"About 600 MB" is not a measurement; "619 MB, measured by `python -c '…'`, output archived at
`evidence/rss-baseline.txt`, population = one process after loading laws_flat.jsonl" is.

- **Baseline before change.** Before altering behavior, the current state MUST be measured and
  archived using the same command that will later re-measure it. The report MUST show
  before-and-after side by side. A post-change number with no pre-change number is not evidence
  of improvement.
- **No invented references.** File paths, function names, field names, and table names MUST be
  confirmed to exist before being cited. When a lookup fails, the failure MUST be reported as a
  failure. A plausible-looking name MUST NOT be used in place of a verified one.
- **Completion requires its own evidence.** A task MUST NOT be marked complete without the
  verification command and that command's actual output. A failed verification means the task is
  not complete.
- **Additive by default.** Changes SHOULD be purely additive and committed independently. When a
  change would alter existing behavior, the agent MUST stop and ask before proceeding.
- **Verified and inferred MUST be distinguishable.** Reporting MUST separate what was measured
  from what was reasoned. Inferences MUST be labeled as such, and MUST NOT be phrased as
  observations.

**Cross-reference**: This principle enforces the falsifiability that Principles VI and XI assume.
Traceability claims are only meaningful if the numbers behind them can be re-derived; a
traceability rule satisfied by an unrerunnable figure is not satisfied.

**Rationale**: An unverifiable number is indistinguishable from a guess, and a guess that reads
like a measurement corrodes exactly the trust that Principles VI and XI exist to protect.

## Technology & Environment Constraints

- **Backend**: Python managed with `uv`; HTTP API via FastAPI.
- **Data**: PostgreSQL for relational data; Qdrant for vector search.
- **Infrastructure**: Docker Compose for coordinated services; Cloudflare for edge/deployment.
- **Environments**: x570, MBP, MSI / WSL (see Principle VIII).
- Changes to this stack (replacing a database, adding a queue, adding a service) are
  architectural decisions and MUST pass the Constitution Check with explicit justification.

## Development Workflow

Non-trivial features SHOULD follow:

```text
/speckit.specify → /speckit.clarify (if ambiguous) → /speckit.plan → /speckit.tasks
→ /speckit.analyze (when useful) → /speckit.implement → tests → review against constitution
```

- Specs define **WHAT and WHY**; plans define **HOW**; tasks define **actionable work**.
  Implementation MUST NOT redefine requirements that belong in the spec.
- **Constitution Check (gate)**: every `plan.md` MUST be checked against Principles I–XII
  before design (Phase 0) and re-checked after design (Phase 1).
- **Violations**: any violation MUST be recorded in the plan's *Complexity Tracking* table,
  stating the principle violated, why it is needed, and the simpler alternative rejected.
  Unjustified violations MUST block the plan.
- **Architectural decision order**: (1) satisfies the current requirement, (2) preserves
  existing contracts, (3) reduces rather than increases complexity, (4) remains testable,
  (5) preserves replaceability where actually needed, (6) preserves reproducibility,
  (7) avoids speculative infrastructure.

### Definition of Done

A feature is complete only when:

- the specified behavior is implemented;
- relevant tests pass and existing tests remain passing;
- public contracts and documentation are updated where necessary;
- no unrelated scope has been introduced;
- no secrets are exposed;
- the implementation is consistent with this constitution.

A feature MUST NOT be considered complete merely because the code compiles or the API starts.

## Governance

This constitution supersedes other project practices. It is intentionally stable.

- **Amendments** MUST be deliberate and documented with: the principle changed, the reason,
  the architectural/operational impact, affected specs and plans, and the new version number.
  Feature requirements MUST NOT modify the constitution implicitly.
- **Versioning** follows semantic versioning: MAJOR for removing or redefining a principle in
  a backward-incompatible way; MINOR for adding a principle or materially expanding guidance;
  PATCH for clarifications and wording fixes.
- **Repeated violations**: if a feature repeatedly requires violating a principle, the
  constitution itself SHOULD be reconsidered rather than letting implementations bypass it.
- **Conflict resolution**: NON-NEGOTIABLE principles (I, III, VI, VII, X, XI, XII) take precedence
  over all others. Among the rest, the order of preference is: Stable Core (II) > Explicit
  Contracts (IV) > Testability (V) > Reproducibility (VIII) > Observability (IX).
- **Compliance review**: all plans, tasks, and implementation reviews MUST verify compliance
  with this constitution.

**Version**: 1.2.1 | **Ratified**: 2026-10-04 | **Last Amended**: 2026-10-10
