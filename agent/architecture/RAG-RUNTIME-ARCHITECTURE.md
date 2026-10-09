# RAG Runtime Architecture — Proposal (Batch 1: DESIGN ONLY)

Status: **PROPOSAL — no implementation, no behavior change.**
Terminal of this batch: `PASS` (accepted) / `FAIL` (issue found) / `STOP` (blocked).

Companion machine-readable file: `runtime-schema.json` (same directory).
Authority extension needed: **none** — this design uses existing component types;
new runtime gates are proposed as *instances* of `gate`, not schema changes.

---

## 1. Architecture overview

Replace the implicit assumption "one host + one fixed model set" with a declarative chain:

```text
Host Profile + Model Profile + Provider + Data Policy + Runtime Mode
      ↓
Execution Planner  (deterministic, policy-first, explainable)
      ↓
Execution Plan     (stages, bindings, index identity, fallback chain)
      ↓
RAG Runtime        (existing backend/app/rag.py + retrieve.py + gateway.py, unchanged in this batch)
```

The planner never mutates models, indexes, or data. It *selects* among registered,
policy-cleared options and emits a plan the runtime executes. Every choice carries
`reasons[]`; every stage transition names its gate; every plan carries provenance
and fingerprints. Future Composer binds the plan; future Console renders it.

## 2. Host Profile schema proposal

One file per host, e.g. `profiles/host-x570.json` (future; not created in this batch).
Fields (`runtime-schema.json#host_profile`):

- `host_id` (`x570` | `mbp` | `msi` | `sabre`), `role` (dev-benchmark / demo / portable-demo / fallback)
- `cpu`, `ram_gb`
- `gpu: { name, vram_gb, unified_memory: bool }`
- `gpu_budget_gb` — **measured usable budget, never spec VRAM.** For unified-memory
  hosts the budget is a separately measured cap, never equated to total unified memory.
- `egress: { lan: bool, wan: bool }` — sabre/backup hosts may be offline-first.
- `measured_at`, `measured_by` — budgets without provenance are rejected by the budget gate.

Proposed (NOT yet measured — planner must treat as placeholders until measured):

| host | GPU | RAM | proposed `gpu_budget_gb` | role |
|---|---|---|---|---|
| x570 | discrete 16 GB | 64 GB | 13 (headroom for desktop/CUDA overhead) | dev, benchmark, full runtime |
| mbp | unified 64 GB | 64 unified | ≤32 RSS cap, CPU-mapped inference preferred | local demo / safety runtime |
| msi | discrete 8 GB laptop | 32 GB | 6 (latency + thermals) | portable demo |
| sabre | discrete 4 GB | 24 GB | 3 | fallback / emergency demo; no large local models assumed |

## 3. Model Profile schema proposal

One entry per model revision in a registry (`runtime-schema.json#model_profile`):

- `model_ref` — opaque binding string resolved at runtime (never a component identity)
- `provider_id`, `role` (`embed` | `rerank` | `generate`), `revision` (digest/pin)
- `origin`: `allowed` | `blocked` | `unknown` — `unknown` ≡ `blocked` until verified
- `capability`: for embed — `dimension`, `metric`, `context_tokens`, `zh_hant_suitability`
  (measured or `unmeasured`); for generate — `context_tokens`, `tool_use`, `judicial_zh` note
- `resource`: `vram_gb_est`, `ram_gb_est`, `throughput_class` (measured or `unmeasured`)
- `license` (must permit the intended use), `provenance[]`

Registry admission rule: policy gate first — excluded-origin or unknown-origin entries
are rejected before resource or quality is ever considered. Local availability
(a model already installed on a host) is **not** admission.

## 4. Provider abstraction

Providers are transport + trust boundaries, not model names (`runtime-schema.json#provider`):

| `kind` | examples in current code | `egress_class` |
|---|---|---|
| `local-daemon` | Ollama on host LAN (`retrieve.py` embed path) | `lan` |
| `local-lib` | in-process inference (future) | `none` |
| `gateway-http` | Cloudflare AI-gateway-routed third parties, OpenRouter-style, vendor endpoints (`gateway.py`, `ZEN`/NVIDIA/Gemini/Groq/Cohere/HF/Mistral routes) | `wan-gated` |
| `external-agent` | OpenCode Go / agent-execution service | `wan-gated` (policy-gated like any external) |

Rules: (a) OpenCode Go is a provider *binding* — its model names never enter component
identities; (b) agent-execution models configured for development assistance are **out
of scope** of the runtime registry and never eligible as RAG bindings unless separately
registered and policy-cleared; (c) every provider declares `auth_ref` (secret name, never
the secret) and `data_policy_class`.

## 5. Runtime Mode model

```text
PERFORMANCE → fastest demonstrable answer; external closed models allowed,
              each external hop gated by the Data Policy Gate.
BALANCE     → planner searches registered options for best value under current
              host + policy + latency + quality; shape is NOT hardcoded
              (local embed + local gen | local retrieval + remote gen |
               local embed + remote rerank + remote gen | fully local).
SAFETY      → egress=none plans only; any external hop FAILs closed.
```

Mode is a planner *input*, recorded in the plan; it is never a code branch in the runtime.

## 6. Data Policy model

Three data classes: `local-only` (judicial corpus, queries) by default; `lan-only`
(host-LAN Ollama/Qdrant traffic); `wan-gated` (explicitly approved external calls with
logged payload class). Decision points, each gated: embedding location, rerank location,
generation location, telemetry/logging destination. SAFETY mode admits only `local-only`
+ `none`-egress providers. A fallback that changes egress class re-evaluates the Data
Policy Gate before executing — degradation never silently widens egress.

## 7. Execution Planner design

Deterministic function (future `planner/`, not built here):

1. **Policy filter** — drop blocked/unknown-origin models and providers disallowed by mode.
2. **Resource filter** — drop options exceeding `gpu_budget_gb`/RAM for the host profile.
3. **Compatibility filter** — embed options must resolve to an existing `index_id`
   (§10); context need (query + retrieved context estimate) must fit model context.
4. **Mode ranking** — PERFORMANCE: minimize expected latency; BALANCE: maximize
   value = quality_evidence / cost under SLO; SAFETY: prefer fully-local, fail closed
   if none fits.
5. Emit plan + ordered fallback chain, each step with `reasons[]`.

Planner version is pinned in plan provenance; same inputs → same plan (replayable).

## 8. Execution Plan schema

`runtime-schema.json#execution_plan`: `plan_id`, `host_id`, `mode`, `planner_version`,
`stages[]` (`embed` → `retrieve` → `rerank?` → `generate`, each with `provider_id`,
`model_ref`, and for embed `index_id`), `fallback[]` (ordered degraded plans),
`budget { vram_gb, latency_ms_slo }`, `gates[]` (verdicts), `provenance { profile
fingerprints, registry fingerprint }`, `terminal` expectation. The runtime executes;
it does not re-plan mid-query except by stepping to the next pre-declared fallback.

## 9. Resource budget model

Budgets are host-profile facts with provenance (§2), not code constants. Planner arithmetic:
`sum(stage vram estimates) + runtime overhead ≤ gpu_budget_gb`, else the option is
infeasible (not "try and OOM"). Estimates marked `unmeasured` are treated at face
value × safety margin 1.5 and flagged in `reasons[]` so measurement debt is visible.
Latency SLOs ride in the plan; violations are observed (§17), never silently absorbed.

## 10. Embedding / index compatibility

Identity chain (non-negotiable):

```text
embedding model revision → dimension → distance metric → index identity
```

`index_id = sha256(model_fingerprint ‖ dimension ‖ metric ‖ collection_config_fingerprint)`.
Consequences: (a) each embedding model resolves to its own collection — sharing one
index across models is a planner **error**, never an optimization; (b) the production
`laws` collection (1024-dim dense + sparse hybrid, cosine-family) is the index of the
*current* production embedding lineage — a new embedding model means a *new* collection,
never in-place re-embedding; (c) frozen experiment lineages (E05-D-A fingerprint
`45a87a…`, E05-D-B `4f4dda…`, E05-D-D selection record) are provenance references only —
the planner may cite their fingerprints, never re-run or re-embed them.

## 11. Fallback strategy

Ordered, pre-declared, gated. Example chain for a constrained host:
full plan → skip rerank → remote-gated generation (PERFORMANCE/BALANCE only; Data
Policy Gate re-evaluated) → smaller local generation → extractive/no-LLM answer
(the existing retrieval `no_match`/abstain path) → explicit refusal. SAFETY chains
contain only egress-`none` steps. A fallback that would cross a policy boundary is
pruned at plan time, not failed at runtime.

## 12. Multi-host deployment strategy

One engine image, four resolved plans. `compose.yaml` services (`qdrant`, `postgres`,
`api`) stay structurally identical; per-host differences flow through the existing
env layer (`settings/env/hosts.shared.env` + per-host inventory) extended in a later
batch with `HOST_ID`, `RUNTIME_MODE`, and profile fingerprints — env keys only, no
logic change here. x570 remains the benchmark host (only host where full-runtime
measurements are authoritative); mbp is the SAFETY reference; msi validates degraded
chains; sabre validates refusal/fallback honesty.

## 13. Composer mapping

Registry: `agent/architecture/` + `agent/registry.json` list profiles/registry/plans as
`artifact` components. Selection: planner inputs match `model_capability` +
permissions. Binding: plan `stages[].model_ref` fills component input ports.
Workflow graph: plan stages render as DAG nodes; fallbacks as `on_state` edges.
Runtime: OpenCode executes builder/verifier roles per existing authority. No Composer
implementation in this batch; this proposal only guarantees the contracts suffice.

## 14. Console observability mapping

Extends the Phase-1 identifiers with runtime fields, all already representable:
`plan_id`, `host_id`, `mode`, per-stage `provider_id`/`model_ref`/`index_id`,
`budget` vs measured, gate verdicts, `terminal`. Render tree:
Project → Host → Plan → Stage → Provider/Model → Index → Gate → Status.
No database, no UI in this batch.

## 15. Security boundaries

Secrets stay in the existing env/sops flow; plans carry `auth_ref` names only. External
hops pass through gateway URLs with per-provider tokens; raw document text never enters
telemetry. The planner is offline-safe: with `wan: false` hosts it must still emit a
valid SAFETY plan or explicit STOP. Policy decisions are allowlist-based; installation
presence is never authorization.

## 16. Failure modes

| failure | detection | response |
|---|---|---|
| VRAM OOM risk | budget gate at plan time | option pruned; reason recorded |
| provider outage | runtime error classification (existing per-provider error paths) | next pre-declared fallback |
| policy block (origin/egress) | policy/data-policy gate | STOP or local-only fallback |
| embed/index mismatch | compatibility filter (`index_id` match) | plan rejected before execution |
| context overflow | context-fit check | truncate-by-policy (recorded) or smaller-context refusal |
| measurement debt | `unmeasured` flags | margin ×1.5 + visible reasons, never silent |

## 17. Implementation phases (later batches, NOT this one)

- P0: host profiles (measured budgets) + model registry skeleton + `index_id` registry.
- P1: planner in dry-run/observe mode (emit plans, execute nothing new).
- P2: runtime honors plans + fallback chains; Console read-only view.
- P3: Composer-assisted plan composition.
Each phase separately gated; any phase may end STOP.

## 18. Explicit non-goals

No production code change; no new index; no model download; no benchmark (embedding or
generation); no frozen-lineage modification; no `opencode.json` or provider-policy change;
no Composer/Console UI; no authority-schema change (proposal only — see below).

## Authority-schema proposal (no modification performed)

No change to `~/projects/agent-arch/component.schema.json` is required: host profiles,
model registries, and execution plans fit the existing `artifact` type; planner data-
policy/budget/compatibility decisions fit the existing `gate` type with `gate_rule`.
If a later batch finds this insufficient, the change request goes through the authority
owner with a versioned proposal — this batch only records that the current schema suffices.

## Architecture decisions (this batch)

- D1 declarative plans over hardcoded modes; D2 allowlist policy-first planning;
- D3 per-model index identity, never shared; D4 fallback chains pre-declared and gated;
- D5 budgets measured-with-provenance, never spec numbers; D6 planner deterministic and
  replayable; D7 OpenCode Go as provider binding, never identity.

## Unresolved questions

- UQ-1: current default reranker revision lineage vs provider policy — flagged for a
  policy-owner review batch; NO change here.
- UQ-2: development-assistance models in `opencode.json` — confirm policy scope boundary
  (dev-assistance vs runtime bindings) with policy owner; NO change here.
- UQ-3: unified-memory budget measurement method for mbp (cap rule pending measurement).
- UQ-4: who measures `throughput_class`/quality evidence for BALANCE ranking (benchmark
  batch design, not this batch).
- UQ-5: declared `index_id` registry vs current runtime collection-capability probing —
  convergence design left to P0.
