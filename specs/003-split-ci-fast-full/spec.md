# Feature Specification: Split CI into Fast (per-PR) and Full (per-merge/daily) Workflows

**Feature Branch**: `003-split-ci-fast-full`

**Created**: 2026-10-07

**Status**: Draft

**Input**: User description: "Split `.github/workflows/ci.yml` into a per-PR fast gate and a per-merge/daily deep verification, with no loss of existing checks and no fabricated implementations"

## Problem Statement

`.github/workflows/ci.yml` is a single workflow with four jobs (`backend`, `frontend`,
`compose`, `guards`, 19 steps total) that runs on every push to `main` and every pull
request. Every PR therefore waits for the entire verification, including work whose cost
buys nothing before merge: the commit-prefix audit requires a full-history clone
(`fetch-depth: 0`) solely to resolve two historical boundary commits.

The cost is real but the *ordering* is wrong. A contributor pushing a one-line change waits
on a full-history clone to learn whether their one-line change is fine. Meanwhile the checks
that genuinely need whole-repository context run at the same cadence as the trivial ones,
so they are neither given room to be thorough nor given a moment when nobody is watching.

The specification that motivated this work assumed a set of CI checks that do not exist in
this repository — a code formatter, security linters, commit-signing verification, and
SOPS/AGE validation jobs. This specification is written against the repository as it
actually is; the non-existent checks are recorded as explicit gaps in §Explicit Gaps
rather than implemented as placeholders.

## Baseline Evidence

Established by running against this repository on 2026-10-07, not assumed:

| Probe | Result |
|---|---|
| `ci.yml` structure | 4 jobs, 19 steps (`backend` 8, `frontend` 5, `compose` 2, `guards` 4) |
| Full `pytest` runtime | 674 passed / 115 skipped / 2 deselected in **16.7 s** |
| `pytest` with only `sops` + `age-keygen` on PATH (what CI installs) | 25 pass, **10 skip** |
| `tests/test_sops_age_invariants.py` with empty `PATH` | **9 pass** — requires no binary |
| `--depth 1` clone: `git ls-files`, `git grep` | work correctly (215 files) |
| `--depth 1` clone: `git log --diff-filter=A -- .githooks/commit-msg` | returns **HEAD**, not the real add-commit `8ca2768` |
| Commit-prefix audit forced to full-branch scan | 368 commits scanned, 139 skipped as pre-rule history, **0 violations** |
| `gh api repos/…/rulesets` | one ruleset, `main-禁刪分支`, exactly one rule: `deletion` |
| `gh api repos/…/branches/main/protection` | **404 — no branch protection exists** |
| `git log --format='%G?'` across 374 commits | **every commit unsigned** (`N`) |

The `--depth 1` result is decisive. On a shallow clone the prefix audit's
`rules_since` boundary silently resolves to `HEAD`, so `git merge-base --is-ancestor "$sha"
"$rules_since"` succeeds for every commit in range and **the guard reports nothing**. This
was reproduced by amending a tip commit to `totally invalid prefix no host code` on a
shallow clone: the guard passed it. The guard does not degrade loudly on shallow history —
it degrades to permanently green, which is the failure mode this repository's own comments
warn against most forcefully.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A Contributor Gets Fast, Trustworthy Feedback on Every PR (Priority: P1)

A contributor opens a pull request against `main`. Within a few minutes they learn whether
their change is acceptable, and the answer covers every check that a pre-merge decision
actually depends on: the test suite, the frontend build, compose interpolation, the IP
policy, Python syntax, file size, and credential patterns. Critically, the feedback is
*trustworthy* — the test suite does not silently skip secret-management tests merely
because the tooling was moved to a slower workflow, and the syntax check covers every
Python file under `backend/app/` rather than only the top level.

**Why this priority**: This is the workflow that runs on every single PR push, which is the
high-frequency iteration loop this repository depends on. Every other story is subordinate to
it: if PR feedback is slow or dishonest, the split has failed even if the deep verification
is thorough.

**Independent Test**: Push a branch with a deliberately invalid commit prefix and a
deliberately broken `backend/app/common/*.py` syntax error, open a PR, and confirm the Fast
workflow reports both failures without the full-history clone and without the deep secret
job.

**Acceptance Scenarios**:

1. **Given** a pull request against `main`, **When** commits are pushed, **Then** the Fast
   workflow runs the backend test suite, the frontend build, compose validation, the IP
   policy check, the Python syntax check, the file-size limit, and the credential scan.
2. **Given** a pull request containing a syntactically invalid file at
   `backend/app/common/<module>.py`, **When** Fast CI runs its syntax check, **Then** that
   file is checked and the run fails — the check covers the whole `backend/app/` tree, not
   only its top level.
3. **Given** a pull request, **When** Fast CI runs the test suite, **Then** the
   secret-rotation tests execute rather than skip, and the run output states the
   expected executed-test count so that a regression to skipping is visible.
4. **Given** a pull request, **When** Fast CI completes, **Then** its slowest job completes
   within 5 minutes.
5. **Given** a commit added to a pull request, **When** Fast CI runs, **Then** no step
   requires full Git history, and no step reports a commit-prefix verdict.

---

### User Story 2 - A Maintainer Gets Deep Verification After Merge and on a Daily Cadence (Priority: P2)

After a change lands on `main`, and independently every day, a maintainer receives
verification that could not be performed cheaply: an audit of commit messages across the
whole repository history, cryptographic verification that the shared credential store is
encrypted as intended and that the encryption round-trip works, and the slow test suite that
no ordinary run has ever executed.

**Why this priority**: These checks are the reason the deep workflow exists, but none of
them can inform a pre-merge decision on a fast loop — the history audit is by definition
about the past, and the slow suite costs minutes for a condition that cannot change within
a single PR. Running them on merge and daily means they always happen and never tax the
iteration loop. Second priority because Story 1 must be trustworthy before depth is added.

**Independent Test**: On a full-history clone, force the commit-prefix audit to scan the
entire branch, and confirm it reports 0 violations across 368 commits while still correctly
rejecting a commit whose prefix is not a declared host code. Then confirm the deep secret
verification fails when a tracked `.enc.env` file is replaced with plaintext.

**Acceptance Scenarios**:

1. **Given** a merge to `main`, **When** the Full workflow runs, **Then** the commit-prefix
   audit executes against a full-history clone and reports any commit whose prefix is not a
   declared host code.
2. **Given** a commit with an invalid prefix at the tip of a full-history clone, **When** the
   audit runs, **Then** the commit is reported rather than silently excluded as history.
3. **Given** the daily schedule, **When** the Full workflow runs with no push event, **Then**
   the audit performs a full-branch scan and completes without reporting pre-rule commits
   as violations.
4. **Given** a tracked `settings/env/*.enc.env` file, **When** deep secret verification runs,
   **Then** the file is confirmed to be encrypted, and an encryption/decryption round-trip
   succeeds using a throwaway key generated inside the workflow.
5. **Given** a tracked `settings/env/*.enc.env` file whose contents are plaintext, **When**
   deep secret verification runs, **Then** the workflow fails.
6. **Given** the daily schedule, **When** the Full workflow runs, **Then** the slow-marked
   tests execute.
7. **Given** a change that breaks only the slow tests, **When** the Fast workflow runs, **Then**
   Fast CI passes — slow-test failures surface in Full CI, not in the PR gate.

---

### User Story 3 - A Maintainer Can Run All Verification Manually From One Place (Priority: P3)

During incident response, or when a scheduled run is suspected of being wrong, a maintainer
can trigger every check from a single entry point without needing to remember which of the
two workflows covers which check.

**Why this priority**: Useful during incidents and cheap to provide, but it is a convenience
over checks that run automatically anyway. It is last because a duplicated copy of every job
would defeat the purpose of the split, and because the dispatcher's own event type changes
what some checks do — a property that must be documented, not silently relied upon.

**Independent Test**: Trigger the unified entry point manually and confirm both the Fast and
Full workflows execute, and that no workflow run is duplicated as a result.

**Acceptance Scenarios**:

1. **Given** the unified entry point is triggered manually, **When** it completes, **Then**
   both the Fast and the Full workflow have executed exactly once each.
2. **Given** the unified entry point, **When** the Full workflow it invoked performs its
   commit-prefix audit, **Then** the audit's behaviour under manual dispatch is documented
   and matches the documented expectation.
3. **Given** a push to `main`, **When** workflows are triggered, **Then** the unified entry
   point does not run, and the Fast and Full workflows each run exactly once.

---

### Edge Cases

- **New branch or force-push where `github.event.before` is the null SHA**: the audit's
  existing logic falls back to a full-branch scan. Verified safe — 368 commits, 0 false
  positives — because both historical boundaries resolve correctly on a full clone.
- **`HOSTS=` unreadable** (unreadable file, or a future sparse checkout): the audit must
  emit a warning and pass rather than block, because at that point the commit is already on
  `main` and a false red trains people toward `--no-verify`. This existing behaviour is
  preserved verbatim.
- **Both Fast and Full triggered by the same push to `main`**: they must use different
  concurrency groups. Sharing one group makes the later run cancel the earlier one, and
  since both fire on the same event the race is real and would intermittently drop the PR
  gate.
- **Manual dispatch of the unified entry point changes `github.event_name`**: a called
  workflow observes the *caller's* event, so the commit-prefix audit's existing
  `if: github.event_name == 'push'` gate would evaluate false and skip the audit. This is
  the single most important property to document on the dispatcher.
- **A test that depends on a locally running stack** (Qdrant) or an untracked artifact:
  these skip in CI and are unrelated to this feature. They must continue to skip rather than
  being made to fail.
- **`.env.example` present but `.env` absent** (clean clone): unaffected; the compose job
  supplies placeholder values as it does today.

## Requirements *(mandatory)*

### Baseline Mapping — every `ci.yml` step MUST have a destination

All 19 steps of the current `ci.yml` are accounted for below. No step may be dropped.

| # | Job | Step | Mechanism | Destination |
|---|---|---|---|---|
| 1 | `backend` | `actions/checkout` | — | **Fast** `backend` |
| 2 | `backend` | `astral-sh/setup-uv` | — | **Fast** `backend` |
| 3 | `backend` | `安裝依賴` (`uv sync --frozen`, `uv pip install ./backend`) | — | **Fast** `backend` |
| 4 | `backend` | `Python 語法檢查` | `ast.parse` over `backend/app/*.py` | **Fast** `backend`, scope widened (FR-004) |
| 5 | `backend` | `IP 準則（無 LAN_IP= 殘留）` | `git grep` | **Fast** `backend` |
| 6 | `backend` | `actions/setup-node` (24) | — | **Fast** `backend` |
| 7 | `backend` | `安裝 sops + age-keygen` | download + `age-keygen` | **Shared** — Fast `backend` and Full `secrets` |
| 8 | `backend` | `pytest` | full suite | **Fast** `backend` |
| 9 | `frontend` | `actions/checkout` | — | **Fast** `frontend` |
| 10 | `frontend` | `pnpm/action-setup` | — | **Fast** `frontend` |
| 11 | `frontend` | `actions/setup-node` (24) + pnpm cache | — | **Fast** `frontend` |
| 12 | `frontend` | `pnpm install --frozen-lockfile` | — | **Fast** `frontend` |
| 13 | `frontend` | `pnpm run build` | — | **Fast** `frontend` |
| 14 | `compose` | `actions/checkout` | — | **Fast** `compose` |
| 15 | `compose` | `docker compose config -q` | env: `TS_IP`, `POSTGRES_PASSWORD`, `HOST_ID` | **Fast** `compose` |
| 16 | `guards` | `actions/checkout` (`fetch-depth: 0`) | full clone | **Full** `guards` |
| 17 | `guards` | `commit 訊息前綴` | pickaxe `-S` + `merge-base --is-ancestor` | **Full** `guards` |
| 18 | `guards` | `單檔不得超過 10 MB` | `git ls-files` + `stat` | **Fast** `security` |
| 19 | `guards` | `追蹤檔案不得含憑證` | `git grep -IE` over tracked files | **Fast** `security` |

Totals: **16 → Fast only, 2 → Full only (rows 16 and 17), 1 → Shared (row 7)**.
19 of 19 accounted for. Row 7's toolchain install runs in **both** workflows — in Fast it
enables the secret-rotation tests, in Full it backs the round-trip verification — so the
number of steps physically present in `fast-ci.yml` is 17 (16 plus row 7). It is counted once
here as Shared to keep the partition disjoint and to prevent it being implemented twice.

### Functional Requirements

- **FR-001**: A Fast workflow MUST run on pushes to `main`, on pull requests targeting
  `main`, and on manual dispatch.
- **FR-002**: A Fast workflow MUST contain jobs covering baseline mapping rows 1–15, 18, 19,
  and the Shared toolchain install of row 7 — that is, 17 of the 19 steps physically present
  in that file. Rows 16 and 17 MUST NOT appear in it.
- **FR-003**: The Fast backend job MUST retain the secret-management toolchain so that
  secret-rotation tests execute rather than skip. Rationale: the suite runs in 16.7 s, so
  there is no time pressure that would justify trading away skip-freedom; and because the
  Full workflow runs on merge and daily but not per-PR, moving these tests there would let
  a broken rotation script merge undetected.
- **FR-004**: The Fast Python syntax check MUST cover every Python file under `backend/app/`,
  including subdirectories. Baseline mapping row 4 currently uses a top-level glob and
  inspects 14 of the 19 files present; the five omitted files are all of `backend/app/common/`.
  The local pre-push hook already uses a recursive search; the two must not diverge.
- **FR-005**: The Fast workflow MUST run no step that requires full Git history, and MUST NOT
  report a commit-prefix verdict.
- **FR-006**: The Fast workflow's slowest job MUST complete within 5 minutes.
- **FR-007**: A Full workflow MUST run on pushes to `main`, on a daily schedule, and on
  manual dispatch.
- **FR-008**: The Full workflow MUST perform the commit-prefix audit against a full-history
  clone, and that audit MUST detect an invalid prefix at the tip of a full-history clone.
- **FR-009**: The Full workflow's commit-prefix audit MUST complete without false positives
  when scanning the entire branch, including when no push event supplies a `before` SHA.
- **FR-010**: The Full workflow MUST verify that every tracked `settings/env/*.enc.env` file
  is encrypted, and MUST perform an encryption/decryption round-trip using a throwaway key
  generated within the workflow. No real age private key may enter CI.
- **FR-011**: The Full workflow MUST execute the slow-marked tests, which no current CI
  configuration has ever executed.
- **FR-012**: The unified entry point MUST support manual dispatch only, MUST contain no
  check implementation of its own, and MUST invoke the Fast and Full workflows without
  causing either to run twice.
- **FR-013**: The Fast and Full workflows MUST use distinct concurrency groups, so that
  their simultaneous execution on the same push cannot cancel one another.
- **FR-014**: The existing concurrency semantics — a new push to the same ref cancels the
  previous in-progress run of the *same* workflow — MUST be preserved for both workflows.
- **FR-015**: No check may be lost. The union of Fast and Full must cover all 19 baseline
  steps, and no step may be implemented in two places such that the copies can diverge.
- **FR-016**: The compose validation step's environment values MUST be carried over
  unchanged, including the placeholder `POSTGRES_PASSWORD` and the `HOST_ID` value that a
  2026-09-27 change made mandatory.
- **FR-017**: The workflow that carries the compose validation step MUST be identified by the
  existing compose-consistency test without that test needing to parse multiple workflow
  files.
- **FR-018**: Actions MUST continue to be pinned to commit SHAs, so that Dependabot's
  existing `github-actions` configuration keeps tracking them.
- **FR-019**: The workflow that reports a step count for the test suite MUST state that
  count in its output, so that a regression from "tests execute" to "tests skip" is visible
  in the run log rather than silent.
- **FR-020**: This feature MUST NOT introduce a code formatter, a linter, commit-signing
  verification, or branch-policy checks. See §Explicit Gaps.

### Key Entities

- **Fast workflow**: the per-pull-request gate. Runs on every PR push. Every check that can
  inform a pre-merge decision, none that requires whole-repository history.
- **Full workflow**: the per-merge and daily deep verification. Carries the checks whose
  subject is the repository as a whole, or whose cost is disproportionate to the PR loop.
- **Unified entry point**: a manual-dispatch-only workflow with no checks of its own that
  invokes both of the above once each.
- **Baseline step**: one of the 19 steps of the pre-split workflow. Every baseline step has
  exactly one owning destination, guaranteeing no silent loss and no divergent duplicate.
- **Historical boundary commit**: one of the two commits that delimit which commit-message
  prefixes are pre-rule history rather than violations. One is the commit that introduced
  the commit-msg hook; the other is the commit at which the retired host code left the
  declared host list. Both are resolved by content-directed search, so both require full
  history to resolve.
- **Explicit gap**: a check named in the motivating specification for which this repository
  has no implementation, no configuration, and no plan. Recorded rather than stubbed.

## Explicit Gaps *(not implemented by this feature)*

These were named in the specification that motivated this work. Each is recorded as a gap
because implementing it would either fabricate a passing check or introduce a permanent
failure, and in both cases the decision belongs to the maintainers.

| Gap | Repository state | Why not implemented here |
|---|---|---|
| Code formatter | No formatter configured or run anywhere; no formatter section in `pyproject.toml`; `frontend/package.json` defines no lint or format script | Adding one introduces a dependency and a failure surface across all existing code, with no current requirement |
| Security linters (as named) | No linter configured. What exists is the credential-pattern scan and the IP-policy check, both already baseline rows 18, 19 | The motivating spec listed these as if they existed; the honest equivalents are already mapped |
| Commit-signing verification | All 374 commits unsigned; no signing key or `commit.gpgsign` configured | A signing check would be red on its first run and red until history is rewritten or a baseline is chosen. That baseline is a maintainer decision, not a CI change |
| Branch-policy checks | One ruleset holds exactly one rule (`deletion`); branch protection returns 404 | There is no policy to verify. Adding checks for absent policies would fabricate coverage |
| SOPS/AGE validation jobs (as named) | No such jobs exist. SOPS and age are installed only to enable tests | The equivalent depth is delivered by FR-010 plus the existing invariant tests; no separate job is invented |

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 19 of 19 baseline steps have a recorded destination; zero steps dropped, zero
  steps implemented twice.
- **SC-002**: The Fast workflow's slowest job completes in under 5 minutes on every one of
  the first 10 pull requests that trigger it.
- **SC-003**: The Fast workflow executes the same set of tests as the pre-split workflow,
  with no increase in skip count attributable to the split.
- **SC-004**: A deliberately invalid commit prefix at the tip of a full-history clone is
  reported by the Full workflow in 100% of attempts.
- **SC-005**: A full-branch scan by the commit-prefix audit reports 0 violations against the
  current 368-commit history, in 100% of attempts.
- **SC-006**: A tracked `.enc.env` file replaced with plaintext causes the Full workflow to
  fail in 100% of attempts.
- **SC-007**: No real age private key is present in any CI environment or log.
- **SC-008**: The pre-existing compose-consistency test passes without being weakened, and
  still fails when `compose.yaml` gains an unsatisfied mandatory variable.
- **SC-009**: Triggering the unified entry point produces exactly one Fast run and exactly
  one Full run.
- **SC-010**: Every action reference in all three workflows is pinned to a 40-character
  commit SHA, verified by automated check.
- **SC-011**: The change touches no file outside `.github/workflows/` and the single
  identified test file, verified by automated check.

## Assumptions

- The Fast and Full workflows are fully independent: neither depends on the other, so a Full
  failure cannot block a pull request and a Fast failure cannot cancel a Full run.
- Only `tests/test_ci_compose_env.py` requires adjustment outside `.github/workflows/`. It
  names the workflow file containing the compose job and would otherwise fail when that job
  moves. Verified by search: no other test or script parses `ci.yml`.
- The existing commit-prefix audit logic is carried over byte-for-byte apart from its
  enclosing job, trigger, and checkout depth. Its behaviour was validated by direct
  simulation rather than re-derived.
- Dependabot's `github-actions` ecosystem is configured for `directory: /`, so the two new
  workflow files are tracked without configuration change.
- The repository remains public, so no CI secret is required by any workflow in this feature,
  and permissions stay read-only.
- No branch protection or required status checks exist today, so renaming workflows and jobs
  cannot break a merge gate. This is recorded as a forward-looking caveat: if required status
  checks are ever enabled, job names become an external contract and this split would break
  it silently.