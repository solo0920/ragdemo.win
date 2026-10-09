# Agent Architecture Adoption — Phase 1

## Boundary

```
~/projects/agent-arch/        architecture authority (definitions, schema, validator)
ragdemo.win/agent/            adoption layer (THIS DIR: registry + workflow instance + validator)
ragdemo.win/.opencode/agents/ OpenCode execution (existing subagents; unchanged)
ragdemo.win/{backend,frontend,ingest,evals,tests}
                              production code ( MUST NOT import or depend on agent/ )
```

Dependency direction: agent-arch definitions → `agent/` metadata → OpenCode execution.
Production code never imports `agent/`. `agent/` never modifies production behavior.

## What Phase 1 does

Represents exactly one real, already-completed workflow — E05-D-D embedding candidate
research — as a workflow *instance* that references the authority components by
`component_id` + `version`, and references its result artifact by path + sha256
(not by copying it). No re-research, no download, no smoke test, no benchmark.

## Files

- `registry.json` — Composer discovery entry: authority location, registered
  components (id → definition path + version), local workflow instances, policy pointer.
- `workflows/e05dd-candidate-research.instance.json` — the run record: node bindings,
  gate verdicts, model-binding pointer, provenance, terminal STOP.
- `scripts/validate_adoption.py` — checks refs, bindings, DAG, provenance, terminal,
  blocked-provider strings, and repo cleanliness.

## Rules preserved from the authority

- No model name appears in any component identity; `model_binding` in the instance is a
  runtime pointer to the repo-local OpenCode config, replaceable without redefining
  any component.
- Provider policy (non-excluded-origin requirement) is referenced, never restated or
  weakened; the instance records the policy gate verdict, not a new policy.
- Every transition names its gate; terminal state is explicit STOP.

## Future Composer mapping

Registry (`registry.json`) → Selection (component `inputs`/`outputs`/`model_capability`/
`permissions` in authority files) → Binding (`bindings` in instance nodes) →
Workflow Graph (`workflow.nodes`/`edges`) → Runtime (OpenCode subagents + gate verdicts).

## Future Console observability mapping

The instance file already carries the minimum stable identifiers:

project_id, workflow_id, workflow_version, task_id, run_id, per-node agent_id,
artifact_id, gate_id list, status, started_at, completed_at, provenance[].
A future Console can render Project → Workflow → Task → Agent → Run → Artifact →
Gate → Status from `registry.json` + `workflows/*.instance.json` with no new schema.
No database or UI is built in this phase.
