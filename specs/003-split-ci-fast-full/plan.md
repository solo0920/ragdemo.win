# Implementation Plan: Split CI into Fast and Full Workflows

**Branch**: `main` | **Date**: 2026-10-07 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/003-split-ci-fast-full/spec.md`

**Note**: Analysis phase. No implementation, no commit, no push.

## Summary

Split the single four-job `.github/workflows/ci.yml` into three files:

- **`fast-ci.yml`** — per-PR gate. Carries 17 of the 19 baseline steps: the whole test
  suite with its secret-management toolchain, the frontend build, compose validation, the IP
  policy, the Python syntax check (widened to the full `backend/app/` tree), the file-size
  limit, and the credential scan. Runs on push to `main`, on pull requests, and on manual
  dispatch. No step requires full Git history.
- **`full-ci.yml`** — per-merge and daily deep verification. Carries the commit-prefix audit
  (the sole genuine full-history consumer, proven to false-negative on a shallow clone),
  cryptographic verification of the shared credential store, and the slow-marked tests that
  no CI configuration has ever executed.
- **`ci.yml`** — thin manual-dispatch-only dispatcher with no checks of its own, invoking the
  other two via `workflow_call`.

The organising rule, derived from the baseline evidence rather than from the motivating
specification's categories: **a check moves to Full only when it needs whole-repository
context, or when its cost is disproportionate to the PR loop. Not when it is merely
secret-related.** Under this rule exactly one step moves, and the toolchain install is shared.

## Technical Context

**Language/Version**: GitHub Actions workflow YAML; actions pinned to commit SHAs

**Primary Dependencies**: `astral-sh/setup-uv`, `actions/setup-node`, `pnpm/action-setup`,
`sops` 3.13.3, `age-keygen` 1.3.2 (pinned by SHA256), `actions/checkout`

**Storage**: N/A — no persistent storage introduced

**Testing**: `pytest` (674 passed / 115 skipped / 2 deselected in 16.7 s locally); workflow
validation is YAML parse + structural assertions + behaviour simulation of the audit logic

**Target Platform**: `ubuntu-latest` GitHub-hosted runners

**Project Type**: CI/CD configuration

**Performance Goals**: Fast slowest job < 5 min; Full < 20 min

**Constraints**: `contents: read` on all workflows; no CI secret required (repo is public);
actions pinned to 40-char SHAs; no real age private key in CI

**Scale/Scope**: 19 baseline steps; 3 workflow files; 1 test file adjusted

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Evaluated against `.specify/memory/constitution.md` (version 1.0.0, ratified 2026-10-04).

| Principle | Compliant | Assessment |
|---|---|---|
| **I. Vertical Slice First** (NON-NEGOTIABLE) | ✅ | Each of the three stories is independently demonstrable: Fast alone gives a working PR gate, Full alone gives deep verification, the dispatcher alone gives manual invocation. No group exists solely to prepare another. |
| **II. Stable Core, Replaceable Components** | ✅ | No core-domain concept is duplicated. The one shared element (toolchain install) is a runner setup concern, not a domain abstraction. No adapter or interface introduced. |
| **III. Simplicity Over Speculation** (NON-NEGOTIABLE) | ✅ | Three files rather than one is *more* files, but each carries a distinct trigger class. The alternative — one file with conditional job-level `if:` expressions — would keep 19 steps in one place and require every reader to evaluate trigger conditions to know what runs when, and would not let Full use a different checkout depth without contaminating the other jobs. No speculative mechanism added. |
| **IV. Contract Before Implementation** | ✅ | The externally observable contract (workflow names, job names, triggers, concurrency groups) is fixed in the spec's baseline mapping and requirements before any file is written. `tests/test_ci_compose_env.py` is an existing contract on the compose job's location and is adjusted explicitly, not incidentally. |
| **V. Test Behaviour, Not Implementation** | ✅ | Each step's behaviour is specified as an outcome ("reports an invalid prefix", "fails on plaintext"), not as a file layout. Behaviour was validated by simulation against the real repository rather than by asserting on YAML structure alone. |
| **VI. RAG Correctness and Traceability** (NON-NEGOTIABLE) | ✅ | No retrieval, generation, citation, or evaluation behaviour is touched. |
| **VII. Security and Data Boundaries** (NON-NEGOTIABLE) | ✅ | The credential store's protection is strengthened, not weakened: the plaintext-detection check is new, and the throwaway key guarantees no real private key enters CI. No secret is committed, logged, or required. Read-only permissions preserved. |
| **VIII. Reproducible Development Environment** | ✅ | No new dependency or infrastructure service is introduced. Runner setup is byte-identical to today's, so all three supported machines remain unaffected. |
| **IX. Observability and Debuggability** | ✅ | The Fast workflow reports its executed-test count so a skip regression is visible in the log (FR-019). The audit retains its stage-level diagnostics. |
| **X. AI-Agent Discipline** (NON-NEGOTIABLE) | ✅ | No production or application code is modified. One test file is adjusted by the minimum required to follow a rename. Two pre-existing defects discovered during analysis are explicitly deferred, not silently fixed. The four named gaps are recorded rather than stubbed. |

**Gate result**: PASS, no violations. Complexity Tracking table omitted.

## Project Structure

### Documentation (this feature)

```text
specs/003-split-ci-fast-full/
├── spec.md      # requirements, baseline mapping, explicit gaps
├── plan.md      # this file
└── tasks.md     # task breakdown with validation
```

### Files Changed (repository root)

```text
.github/workflows/
├── ci.yml         # REWRITTEN: 19 steps → 0 steps. dispatch-only dispatcher.
├── fast-ci.yml    # NEW: jobs backend, frontend, compose, security
└── full-ci.yml    # NEW: jobs guards, secrets, test-full

tests/
└── test_ci_compose_env.py   # ONE constant + assert message retargeted
```

**Structure Decision**: Three sibling files under the existing `.github/workflows/`
directory. No new directory, no nesting, no configuration change. The existing `dependabot.yml`
is untouched and already covers `directory: /` for the `github-actions` ecosystem.

## Phase 0: Research Findings

All findings below were produced by running against this repository on 2026-10-07.

### R-1: The commit-prefix audit false-negatives on a shallow clone

`ci.yml:236` gates the audit on `if: github.event_name == 'push'` and the `guards` job
checks out with `fetch-depth: 0`. The depth is load-bearing. On a `--depth 1` clone:

```
$ git log --diff-filter=A -1 --format='%H' -- .githooks/commit-msg
5d6ad09…   # ← HEAD
$ git log --diff-filter=A -1 --format='%H' -- .githooks/commit-msg   # full history
8ca2768…   # ← the real "hook added" commit
```

`rules_since` therefore resolves to `HEAD`, so `git merge-base --is-ancestor "$sha"
"$rules_since"` succeeds for every commit in range, and the guard skips all of them.
Reproduced end-to-end: amending a tip to `totally invalid prefix no host code` on a shallow
clone produced no violation. **The guard degrades to permanently green rather than to
visibly broken.**

This is the sole reason the audit moves to Full CI. It is not a cost judgement.

### R-2: The audit is safe as a full-branch scan

Required because Full CI's daily schedule supplies no `github.event.before`, forcing the
existing fallback `range="$AFTER"` — i.e. a whole-branch scan. Simulated against real
history with the audit's own logic and its own boundary lookups:

```
hosts_raw   = x570|mbp|wsl
new_since   = a8879dc…   rules_since = 8ca2768…
scanned=368  skipped_as_history=139  violations=0
```

139 commits predate either boundary and are correctly excluded; zero false positives. The
earlier 139-report regression documented in `ci.yml:282-288` is exactly this path, and the
current boundary logic handles it.

### R-3: Toolchain dependency map

| Test file | Needs | CI today |
|---|---|---|
| `test_rotate_secret.py` | `sops` + `age-keygen` + key | **runs** (25 tests) |
| `test_sops_age_invariants.py` | nothing (verified with empty `PATH`) | **runs** (9 tests) |
| `test_backup_env.py` | `age` (the encryptor, not the keygen) | **skips** (10 tests) — pre-existing, out of scope |
| 12 files | `node` | runs via `setup-node` |

The motivating spec listed "SOPS secret validation" and "AGE secret encryption checks" as
distinct CI jobs. They are not jobs. SOPS and age are installed solely to enable tests, and
the invariant tests need no binary at all. The deep-verification requirement (FR-010) is
specified as new behaviour rather than assumed to exist.

### R-4: `sops filestatus` works with no key

```
$ env -i PATH=/usr/bin:/bin sops filestatus settings/env/secrets.common.enc.env
{"encrypted":true}
```

One tracked `.enc.env` file exists. No test currently asserts this, so FR-010 is new
coverage. Note: `sops -e` requires `--output` (it writes to stdout otherwise) and `sops -d`
requires the target repeated positionally after `--filename-override` — verified, and the
reason is already documented at `tests/test_rotate_secret.py:90-92`.

### R-5: No merge gate can break

`gh api repos/solo0920/ragdemo.win/rulesets` → one ruleset, one rule, `deletion`.
`gh api …/branches/main/protection` → 404. No `required_status_checks` exists, so renaming
workflows and jobs cannot break a merge gate today. Recorded as a forward-looking caveat: if
required checks are ever enabled, job names become an external contract.

### R-6: One test constrains the compose job's location

`tests/test_ci_compose_env.py:21` hardcodes `.github/workflows/ci.yml` and greps that file for
the compose step's `env:` block by anchoring on `- name: docker compose config` and
`run: docker compose config`. A repository-wide search found no other test or script that
parses `ci.yml`. This is the only reason FR-017 exists.

### R-7: Concurrency groups must differ

Fast and Full both fire on push to `main`. Sharing one concurrency group would make whichever
starts second cancel the first — a genuine race on every merge. Distinct groups required
(FR-013).

### R-8: `github.event_name` under `workflow_call`

A called workflow observes the **caller's** event. Dispatching `ci.yml` makes
`github.event_name == 'workflow_dispatch'`, so the audit's existing `if: github.event_name ==
'push'` gate evaluates false and the audit **skips**. This is not a bug to fix here — the
audit has no meaningful before/after range under manual dispatch — but it must be documented
on the dispatcher so nobody reads a green manual run as a completed audit.

### R-9: The two deferred defects

1. `ci.yml:119` installs `age-keygen` only, but `tests/test_backup_env.py:38` gates on
   `age`. All 10 tests skip in CI today. `scripts/backup-env.sh:111` calls `age -r`, so this
   script's encryption path is unexercised by CI. Verified by running the suite with `age`
   removed from `PATH`.
2. `ci.yml:56` checks `backend/app/*.py` (14 files) while `.githooks/pre-push:31` uses a
   recursive search (19 files). The five omitted files are all of `backend/app/common/` — the
   package that was cut *for* the very class of bug this misses.

Defect 2 is fixed here because FR-004 is inseparable from Fast CI's correctness claim.
Defect 1 is deferred per scope decision; it belongs to the Full `secrets` job's design and
would otherwise mix an unrelated repair into this change.

## Phase 1: Design

### Workflow Topology

```text
                    ┌──────────────────────┐
  push:main ───────►│  fast-ci.yml         │  group: fast-ci-${{ github.ref }}
  pull_request ────►│  backend / frontend  │  cancel-in-progress: true
  dispatch ────────►│  compose / security  │
  workflow_call ───►└──────────────────────┘
                           ▲
                           │ called by
                    ┌──────┴───────────────┐
  dispatch ────────►│  ci.yml              │  0 jobs of its own
                    │  (thin dispatcher)    │
                    └──────┬───────────────┘
                           │ calls
                    ┌──────▼───────────────┐
  push:main ───────►│  full-ci.yml         │  group: full-ci-${{ github.ref }}
  schedule (daily) ►│  guards / secrets    │  cancel-in-progress: true
  dispatch ────────►│  test-full           │
  workflow_call ───►└──────────────────────┘
```

Fast and Full are siblings with no edge between them: neither `needs:` the other, so a Full
failure cannot block a PR and a Fast failure cannot cancel a Full run. `ci.yml` sits above
both with no checks of its own.

### Job Design

| Workflow | Job | Contents | Checkout depth |
|---|---|---|---|
| `fast-ci.yml` | `backend` | `uv sync` → syntax check (FR-004) → IP policy → `setup-node` → sops+age-keygen → `pytest` + count report (FR-019) | default |
| `fast-ci.yml` | `frontend` | `pnpm install --frozen-lockfile` → `pnpm run build` | default |
| `fast-ci.yml` | `compose` | `docker compose config -q`, env byte-identical (FR-016) | default |
| `fast-ci.yml` | `security` | file-size limit → credential pattern scan | default |
| `full-ci.yml` | `guards` | `fetch-depth: 0` → commit-prefix audit | **full** |
| `full-ci.yml` | `secrets` | sops + age-keygen → `filestatus` on tracked `.enc.env` → throwaway-key round-trip | default |
| `full-ci.yml` | `test-full` | full suite with slow marks enabled | default |

### Verification Approach

Each requirement is verified by a check that would fail if the requirement were violated,
not by inspecting the diff:

| Requirement | Verification |
|---|---|
| SC-001 | YAML-parse all three workflows; extract every step; assert the union equals the 19-row baseline mapping and that no check step appears in two files |
| SC-002/SC-003 | Compare Fast's `pytest` invocation and toolchain against pre-split `ci.yml` line by line |
| SC-004 | On a full-history clone, amend a throwaway commit's message to an invalid prefix and run the audit script; assert it is reported |
| SC-005 | Run the audit script in full-branch mode against real history; assert `violations=0` |
| SC-006 | Copy `settings/env/secrets.common.enc.env`, replace with plaintext, run the filestatus check; assert non-zero exit |
| SC-007 | Confirm the round-trip key is generated inside the workflow and no real key path is referenced |
| SC-008 | Run `tests/test_ci_compose_env.py` unmodified against the new layout; then temporarily add `${NEW:?}` to a copy of `compose.yaml` and assert it still fails |
| SC-009 | Confirm `ci.yml` has no `push`/`pull_request`/`schedule` trigger and no `jobs` definitions other than two `uses:` entries |
| SC-010 | Assert every `uses:` value matches `^[a-z0-9-]+/[a-z0-9-]+@[0-9a-f]{40}$` |
| SC-011 | `git diff --name-only` against a baseline commit; assert only the four allowed paths |

SC-004 and SC-005 reuse the audit script from R-2, so the audit's behaviour is validated by
execution rather than by reading YAML.

### Decisions Carried Forward

1. **Three files, not one with conditional jobs.** Justified under Principle III above;
   recorded because it is the main structural choice.
2. **The audit's logic is copied, not reimplemented.** Its history-boundary behaviour is
   subtle and already repaired twice (2026-10-01, and the 139-report regression). Copying
   byte-for-byte keeps one source of truth per workflow and avoids re-deriving a solved
   problem.
3. **`ci.yml` retains no jobs.** The motivating spec listed it as "archived"; a dispatcher
   that still contained copies would reintroduce the drift that `ci.yml:282-288` documents.
4. **FR-004 is in scope; the `age` defect is not.** FR-004 is part of making Fast CI's
   correctness claim true, which is Story 1's acceptance criterion 2. The `age` defect is an
   independent pre-existing repair.

## Complexity Tracking

> No Constitution Check violations. Table intentionally omitted.

## Out of Scope

- Production or application code (none).
- The `age` / `test_backup_env.py` skip defect — deferred to the Full `secrets` job.
- Code formatter, linter, commit-signing verification, branch-policy checks — recorded as
  explicit gaps in `spec.md`.
- `ARCHITECTURE.md` lines 165-167 and 45 reference `ci.yml` job names and state "三 job"
  (there are four). Updating them is documentation work that follows the split, not part of
  it.
- Enabling `required_status_checks`, which remains a deliberate omission per
  `ARCHITECTURE.md:178`.