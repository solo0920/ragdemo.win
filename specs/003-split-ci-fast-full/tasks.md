# Tasks: Split CI into Fast and Full Workflows

**Input**: Design documents from `/specs/003-split-ci-fast-full/`

**Prerequisites**: plan.md (required), spec.md (required for user stories)

**Tests**: Included where the specification states a behaviour that must be provably enforced.
Each test-style task is a shell/YAML assertion run against the real repository, not a unit
test added to `tests/`.

**Organization**: Tasks are grouped by user story. Each group is independently verifiable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: User story this task belongs to (US1, US2, US3)
- Paths are repository-root-relative

---

## Phase 1: Baseline Capture (Blocking)

**Purpose**: Freeze the pre-split state so every later task can prove it lost nothing.

**⚠️ CRITICAL**: No workflow may be rewritten until the baseline is captured, because the
baseline is the only record of what `ci.yml` contained before it was emptied.

- [X] **T001** Capture the authoritative step baseline: YAML-parse `.github/workflows/ci.yml`,
      enumerate all steps with `job`, `name`/`uses`, and `run`, and write the result to
      `specs/003-split-ci-fast-full/baseline-steps.txt`. Assert the total is exactly 19
      (`backend` 8, `frontend` 5, `compose` 2, `guards` 4).
      *Validation*: the captured file contains 19 step records and no line reads `(empty)`.
      **Result 2026-10-07**: 19 records, 0 empty, `guards` recorded at `checkout_depth: 0`.
- [X] **T002** Save the pre-split `ci.yml` verbatim to `specs/003-split-ci-fast-full/ci.yml.orig`
      so the exact audit logic remains reviewable after `ci.yml` is rewritten.
      *Validation*: `diff <(git show HEAD:.github/workflows/ci.yml) ci.yml.orig` is empty, and
      `grep -cE '^\s+fetch-depth:' ci.yml.orig` returns 1.
      **Result 2026-10-07**: diff empty, 416 lines. Note the task originally specified
      `grep -c 'fetch-depth: 0'` returning 1; that returns 2 because line 233's **comment**
      quotes the setting. The validation was corrected to anchor on line-leading whitespace so
      it counts only active config — active count is 1 (line 210).

**Checkpoint**: Baseline frozen. Proceed to US1.

---

## Phase 2: User Story 1 - Fast PR Gate (Priority: P1) 🎯 MVP

**Goal**: `fast-ci.yml` exists, runs on push/PR/dispatch, and covers baseline rows 1-15, 18, 19
with no full-history dependency and no silent skips.

**Independent Test**: Push a branch containing an invalid `backend/app/common/*.py` syntax
error; confirm Fast CI fails naming that file, and that no step required a full-history clone.

### Tests for User Story 1 (write first, confirm they fail before implementation)

- [X] **T003** [P] [US1] Write the baseline-coverage assertion as an executable check:
      extract every step from `fast-ci.yml`, and assert that each of baseline rows 1-15, 18, 19
      from `spec.md` is present, and that rows 16-17 (full-history checkout, prefix audit) are
      **absent**. Place at `specs/003-split-ci-fast-full/check-coverage.sh`.
      *Validation*: run it now against the absent `fast-ci.yml` and confirm it fails with a
      missing-file error — a check that cannot fail before implementation is not a check.
      **Result 2026-10-07**: exit 1, 15 `::error::` lines naming every missing row, plus the
      pre-split `ci.yml` correctly reported as not-yet-a-dispatcher. Confirmed it has teeth.

### Implementation for User Story 1

- [X] **T004** [US1] Create `.github/workflows/fast-ci.yml` with `name`, `on:` (`push`
      branches `[main]`, `pull_request` branches `[main]`, `workflow_dispatch`,
      `workflow_call`), `permissions: contents: read`, and
      `concurrency: group: fast-ci-${{ github.ref }}` with `cancel-in-progress: true`.
      No jobs yet.
      *Validation*: `python3 -c "import yaml;d=yaml.safe_load(open('.github/workflows/fast-ci.yml'));print(d[True])"`
      prints exactly the four triggers; `d['concurrency']['group']` starts with `fast-ci-`.
      **Result 2026-10-07**: exactly the four triggers, both branch filters `[main]`, group
      `fast-ci-${{ github.ref }}`, `cancel-in-progress: true`, `permissions: contents: read`,
      `jobs: {}`.
- [X] **T005** [P] [US1] Add the `backend` job to `fast-ci.yml`: `actions/checkout` (default
      depth), `astral-sh/setup-uv` with cache, `uv sync --frozen` + `uv pip install ./backend`,
      `actions/setup-node` at `'24'`. Copy each `uses:` value and its pinned SHA from
      `ci.yml.orig` verbatim.
      *Validation*: every `uses:` matches `^[a-z0-9-]+/[a-z0-9-]+@[0-9a-f]{40}$`; the four
      pinned SHAs equal those in `ci.yml.orig`.
      **Result 2026-10-07**: `backend` job created with checkout (no `fetch-depth`), setup-uv
      with cache, `uv sync --frozen` + `uv pip install ./backend`. All `uses:` in the file
      verified against the 40-hex-SHA regex and confirmed to be a subset of `ci.yml.orig`'s.
- [X] **T006** [P] [US1] Add the `frontend` job to `fast-ci.yml`: `pnpm/action-setup` with
      `package_json_file: frontend/package.json`, `actions/setup-node` at `'24'` with pnpm cache
      and `cache-dependency-path: frontend/pnpm-lock.yaml`, `pnpm install --frozen-lockfile`,
      `pnpm run build` (both with `working-directory: frontend`).
      *Validation*: SHAs equal `ci.yml.orig`; the two `run:` steps both carry
      `working-directory: frontend`.
      **Result 2026-10-07**: 5 steps; both `run:` steps carry `working-directory: frontend`.
- [X] **T007** [P] [US1] Add the `compose` job to `fast-ci.yml`, carrying the `docker compose
      config -q` step over byte-identically: same step name, same `run:`, and the same `env:`
      block containing `TS_IP: 100.119.83.111`,
      `POSTGRES_PASSWORD: ci-placeholder-not-a-real-secret`, `HOST_ID: wsl`.
      *Validation*: extract the `env:` block and assert the three keys and values are present;
      `tests/test_ci_compose_env.py` passes when temporarily retargeted (or its target file is
      already updated — whichever applies at execution time).
      **Result 2026-10-07**: env parsed as exactly `{TS_IP: 100.119.83.111,
      POSTGRES_PASSWORD: ci-placeholder-not-a-real-secret, HOST_ID: wsl}`, run is
      `docker compose config -q`, step name unchanged. The compose-consistency test itself is
      retargeted at T025; the anchors it greps for (`- name: docker compose config` and
      `run: docker compose config`) are both present here.
- [X] **T008** [P] [US1] Add the `security` job to `fast-ci.yml` with baseline rows 18 and 19:
      the 10 MB per-file limit (`git ls-files` + `stat -c%s`) and the four-pattern credential
      scan (`git grep -nIE`), copying both step bodies verbatim from `ci.yml.orig` including
      their explanatory comments.
      *Validation*: both step names present; the `PATTERNS` heredoc contains exactly four
      labelled patterns matching `ci.yml.orig`.
      **Result 2026-10-07**: 3 steps (checkout + 2 checks). Both `run:` bodies compared against
      `ci.yml.orig` and confirmed **byte-identical**. All four patterns (`cfut_`, `nvapi-`,
      `sk-or-v1-`, `hf_`) present with their labels.
- [X] **T009** [US1] Add the remaining `backend` steps to `fast-ci.yml`: the sops +
      age-keygen install (both pinned SHA256 sums unchanged), and the IP-policy `git grep` for
      `^LAN_IP=`.
      *Validation*: both SHA256 lines are byte-identical to `ci.yml.orig`; the IP-policy step
      still greps `^LAN_IP=` over the working tree.
      **Result 2026-10-07**: both SHA256 sums verified present and identical to `ci.yml.orig`;
      `V_SOPS=3.13.3; V_AGE=1.3.2` unchanged; `SOPS_AGE_KEY_FILE=/tmp/agekey.txt` set to the
      throwaway key; IP-policy step still greps `^LAN_IP=`.
- [X] **T010** [US1] Add the Python syntax check to `fast-ci.yml`'s `backend` job, **widened**
      per FR-004: replace the `for f in backend/app/*.py` glob with a recursive
      `find backend/app -name '*.py' -type f`, preserving the `ast.parse` call and the
      accumulate-then-fail structure. Port the explanatory comment from
      `.githooks/pre-push:29-33`.
      *Validation*: run the step's command against the working tree — it reports 19 files and
      exits 0; then inject a syntax error into a temporary copy at
      `backend/app/common/text.py` and confirm the loop reports that path and exits non-zero.
      **Result 2026-10-07**: happy path checks **19 files**, exit 0 (old glob: 14). Mutation:
      appended `def broken(:` to `backend/app/common/text.py` → `::error::語法錯誤
      backend/app/common/text.py`, exit 1. File restored; `git diff -- backend/` empty.
- [X] **T011** [US1] Add the `pytest` step to `fast-ci.yml`'s `backend` job as
      `.venv/bin/pytest -q -p no:cacheprovider`, and add the executed-test-count reporting
      required by FR-019 so a skip regression is visible in the log.
      *Validation*: run the step's command locally; it completes and prints a test count;
      `grep -c` for the count-reporting line is non-zero.
      **Result 2026-10-07**: step placed last in the job (after setup-node and the toolchain —
      it must not run before them). Real run: **674 passed, 115 skipped, 2 deselected in
      16.14s**, matching the baseline exactly. `::notice title=pytest 實際執行數` present,
      wrapped with `set -o pipefail` so the pipeline still fails the step on test failure.
- [X] **T012** [US1] Port the explanatory comments from `ci.yml.orig` to the corresponding
      `fast-ci.yml` steps — especially the ones recording *why* the toolchain is installed (28
      tests would otherwise silently skip) and the two differing SHA256 provenance strengths.
      *Validation*: for each of the 8 `backend` steps and 5 `frontend` steps in `ci.yml.orig`,
      a corresponding comment-bearing step exists in `fast-ci.yml`; a reader of `fast-ci.yml`
      can answer "why is sops installed here?" without opening `ci.yml.orig`.
      **Result 2026-10-07**: 14 comment topics asserted present, 0 missing — including the
      sops-not-in-apt note, the `age-keygen`-not-bundled rationale, the disposable-key
      rationale, the type-stripping/node-version caveat, the asymmetric SHA256 provenance, the
      `uv run` rationale, the compose `HOST_ID` lesson, and the msi→wsl rename note.
      Confirmed answerable from `fast-ci.yml` alone: the 28-test silent-skip reasoning is
      present in the file that contains the install.
- [X] **T013** [US1] Run `specs/003-split-ci-fast-full/check-coverage.sh` and confirm it passes.
      *Validation*: exit code 0; its output names all 17 baseline rows physically present in
      `fast-ci.yml` (rows 1-15, 18, 19 — which is 16 Fast-only rows plus Shared row 7) and
      confirms rows 16-17 are absent.
      **Result 2026-10-07 — exit 0, PASS.** Re-run after T022. Output: 12 `ok` Fast
      fingerprints, `shared toolchain present in both`, 2 `ok` Full fingerprints, and the
      dispatcher-shape checks green. Rows 16-17 confirmed absent from `fast-ci.yml` and present
      in `full-ci.yml`.
      **Three bugs found in the check itself while running it** (all fixed; the check is the
      deliverable, so a check that passes for the wrong reason is worse than none):
      1. `strip_comments f | grep -q …` under `pipefail` — `grep -q` exits on first match,
         upstream `sed` dies with SIGPIPE 141, and `pipefail` makes a **present** row report as
         MISSING. This is the exact trap `.githooks/pre-push:60-70` documents. Replaced with
         `matches()`, which slurps into a variable and uses a `case` glob.
      2. The no-leak rule flagged `setup-uv` / `setup-node` / `uv sync` / `pytest` as
         "duplicated checks". They are **scaffolding**, and duplicating scaffolding across two
         workflows is correct — different runners, different selections. Split the fingerprints
         into `SCAFFOLDING` vs checks and apply the no-leak rule only to checks.
      3. The dispatcher check tested `^\s+(run|name):`, so a valid dispatcher's job-level
         `name:` was reported as "contains steps of its own". Now tests for `steps:` and
         `run:` specifically, plus a count that exactly 2 `uses:` references are present.
      Added a **test-selection partition** assertion, because "pytest appears in both files"
      should not be taken on trust: `fast-ci.yml` must NOT carry `-o addopts= -m slow`, and
      `full-ci.yml` MUST. Verified independently — fast = 789 non-slow tests, full = the 2 slow
      ones, 789 + 2 = 791 total, so no test executes twice.

- [X] **T014** [P] [US2] Write the audit-behaviour assertions as an executable script at
      `specs/003-split-ci-fast-full/check-audit.sh`, with two modes. Mode 1 (detection): given a
      full-history clone whose tip commit message has an invalid prefix, assert the audit
      reports it. Mode 2 (no false positives): run the audit in full-branch mode against real
      history and assert `violations=0` and `skipped_as_history>0`. Reuse the boundary-lookup
      logic verbatim from `ci.yml.orig`.
      *Validation*: mode 1 fails against the current (pre-split) shallow-checkout assumption and
      mode 2 passes — confirm both directions before relying on the script.
      **Result 2026-10-07 — both directions confirmed**:
      `clean` on real full history → `scanned=368 skipped_as_history=139 violations=0`, exit 0.
      `detect` on a full-history throwaway clone with tip
      `totally invalid prefix and no host code` → `VIOLATION: 17b28afe …`, exit 0.
      Negative direction also confirmed: `detect` on a **`--depth 1`** clone with the same
      invalid tip → `rules_since` resolves to HEAD, `SHALLOW-CLONE-SIGNATURE` printed,
      `violations=0`, and the script correctly refuses to pass. This is plan.md R-1 reproduced
      end-to-end, and it is the empirical justification for `fetch-depth: 0` in T016.

### Implementation for User Story 2

- [X] **T015** [US2] Create `.github/workflows/full-ci.yml` with `name`, `on:` (`push` branches
      `[main]`, `schedule` with a daily `cron`, `workflow_dispatch`, `workflow_call`),
      `permissions: contents: read`, and
      `concurrency: group: full-ci-${{ github.ref }}` with `cancel-in-progress: true`.
      *Validation*: the concurrency group starts with `full-ci-` and differs from
      `fast-ci-${{ github.ref }}`; `schedule` is present with exactly one `cron` entry.
      **Result 2026-10-07**: exactly the four triggers; `schedule` has one entry,
      `cron: '23 4 * * *'` (off-the-hour, commented); group `full-ci-${{ github.ref }}`
      confirmed **different** from `fast-ci-${{ github.ref }}`; `permissions: contents: read`.
- [X] **T016** [US2] Add the `guards` job to `full-ci.yml`: `actions/checkout` with
      `fetch-depth: 0`, and the commit-prefix audit step copied **byte-for-byte** from
      `ci.yml.orig` including all explanatory comments and the `if: github.event_name == 'push'`
      gate.
      *Validation*: `check-audit.sh` mode 2 reports `violations=0`; the audit step's `run:` body
      is byte-identical to `ci.yml.orig`'s (compare after normalising indentation).
      **Result 2026-10-07**: 2 steps. Checkout carries `fetch-depth: 0`. The audit step's `run:`
      body, `name`, and `if: github.event_name == 'push'` gate all compared against
      `ci.yml.orig` and confirmed **byte-identical**. `check-audit.sh clean` →
      `violations=0`, exit 0. The `fetch-depth: 0` line carries a comment stating it is
      load-bearing and that removing it yields "no audit", not "a looser audit".
- [X] **T017** [P] [US2] Add the `secrets` job to `full-ci.yml`: install `sops` 3.13.3 and
      `age-keygen` 1.3.2 with the same pinned SHA256 sums as `fast-ci.yml`.
      *Validation*: both SHA256 lines are byte-identical to `fast-ci.yml`'s; no real age private
      key path appears anywhere in the job.
      **Result 2026-10-07**: `secrets` job created (checkout + toolchain). Both SHA256 pins
      extracted and compared to `fast-ci.yml`'s — identical:
      `e5bec334…fef6b sops`, `cbe24006…6ac10 age.tgz`. The asymmetric-provenance comment
      carried over. No real key path (`~/.config/sops/age/keys.txt`) anywhere in the file.
      Job header states why this exists: an encrypted file decrypted and committed back turns
      credentials into plaintext in a **public** repo's history, and push protection does not
      catch prefix-less keys — so nothing existing goes red.
- [X] **T019** [P] [US2] Add the `test-full` job to `full-ci.yml`: `uv sync --frozen`,
      `uv pip install ./backend`, `actions/setup-node`, and the full suite with slow marks
      enabled (overriding `addopts = "-m 'not slow'"` from `pyproject.toml`).
      *Validation*: the pytest invocation includes a slow-mark override; `pytest --collect-only -m slow`
      locally lists exactly the slow-marked tests, confirming the override selects a non-empty set.
      **Result 2026-10-07**: 5 steps. Invocation is
      `pytest -q -p no:cacheprovider -o addopts= -m slow`. Verified both directions:
      with the override → **2/791 collected** (`test_pg_timeout_real.py`, both tests); with
      the repo's default `addopts` → **2 deselected**. So those two tests have never run in
      any CI and now will. Comment notes `-o addopts=` must replace the flag wholesale —
      two `-m` flags conflict, so clearing `addopts` is required, not `-m slow` alone.
      - [X] **T018** [US2] Add the encryption-verification step to `full-ci.yml`'s `secrets` job:
      generate a throwaway age key inside the workflow, assert `sops filestatus` reports
      `encrypted: true` for every tracked `settings/env/*.enc.env`, then perform an
      encrypt/decrypt round-trip with the throwaway key. Note in a comment that `sops -e`
      requires `--output` and `sops -d` requires the target repeated positionally after
      `--filename-override`, and that no real private key may enter CI.
      *Validation*: run the step's commands locally with a throwaway key — filestatus reports
      `encrypted:true` and the round-trip exits 0; then copy `settings/env/` to a temp dir,
      replace the `.enc.env` content with plaintext, and confirm filestatus reports
      `encrypted:false` and the step exits non-zero.
      **Result 2026-10-07 — all three directions, executed from the workflow's own `run:` bodies**
      (extracted via YAML, not retyped): (a) real tracked file → `{"encrypted":true}`,
      "已檢查 1 個加密檔", exit 0. (b) round-trip step verbatim → `{"encrypted":true}` +
      "✓ sops + age 往返成立", exit 0. (c) plaintext fixture → `{"encrypted":false}` +
      `::error title=加密檔已變成明文` + runbook pointer, exit 1. Fixtures cleaned up; real
      repo untouched.
      **Two bugs found by executing rather than reading** (both fixed and commented in-file):
      1. `sops filestatus` returns **exit 0 even when it prints `{"encrypted":false}`**. A
         `set -e` guard would never fire. The step parses the JSON output instead.
      2. My first plaintext assertion was `grep '^RAGDEMO_CI_ROUNDTRIP='`, which **matches
         the encrypted form too** — sops dotenv preserves the key name and encrypts only the
         value (`RAGDEMO_CI_ROUNDTRIP=ENC[AES256_GCM,data:…]`). It false-positived on a
         correctly encrypted file. Now asserts `=ENC[`.
      The step also fails loudly if `git ls-files` finds zero `.enc.env` files, so it cannot
      pass by having nothing to check.

**Parallel note**: T017 and T019 ran in parallel (distinct jobs); T018 then extended the job
T017 created.
- [X] **T020** [US2] Port the explanatory comments from `ci.yml.orig` to `full-ci.yml`'s `guards`
      and `secrets` jobs — in particular why `fetch-depth: 0` is load-bearing and why a
      shallow clone would silently report nothing.
      *Validation**: the `guards` job contains a comment explaining that the audit's boundary
      lookup requires full history and degrades to reporting nothing on a shallow clone.
      **Result 2026-10-07**: 13 comment topics asserted present. Carried into `guards`: the
      load-bearing `fetch-depth: 0` rationale (with the reproduced R-1 evidence), "it degrades
      to permanently green, not visibly broken", "removing it gives you no audit, not a looser
      audit", the pickaxe `^` anchor warning, the full-width-colon character-class trap, the
      139-false-positive history, the ruleset 422 explanation, and the read-only-permissions
      note. Carried into `secrets`: no-real-key-in-CI, both `sops` CLI traps, and the
      `filestatus`-exits-0 trap. Header explains why this file exists and why the audit is not
      in the PR gate.
      One topic intentionally **not** duplicated: the `secret_scanning_non_provider_patterns`
      / push-protection gap belongs with the credential scan, which lives in `fast-ci.yml`'s
      `security` job — it travelled with that step at T008 and is verified present there.
      Copying it here would be a second copy of one idea, which is the drift failure mode this
      repository's own comments warn about.
- [X] **T021** [US2] Run `check-audit.sh` in both modes and confirm both pass against
      `full-ci.yml`.
      **Result 2026-10-07**: `clean` → `scanned=368 skipped_as_history=139
      violations=0`, exit 0. `detect` → `VIOLATION: 17b28afe totally invalid prefix and
      no host code`, exit 0. Separately confirmed the `guards` job checks out at
      `fetch-depth: 0` and its audit `run:` body is byte-identical to `ci.yml.orig` —
      so the logic the script exercises and the logic the workflow runs are the same.
      *Validation**: mode 1 reports the invalid prefix; mode 2 reports `violations=0`.

**Checkpoint**: US1 and US2 are independently functional. US3 can proceed.

---

## Phase 4: User Story 3 - Unified Manual Entry Point (Priority: P3)

**Goal**: `ci.yml` becomes a thin manual-dispatch-only dispatcher with no checks of its own.

**Independent Test**: Trigger `ci.yml` manually and confirm exactly one Fast run and one Full
run result.

### Implementation for User Story 3

- [X] **T022** [US3] Rewrite `.github/workflows/ci.yml` as a dispatcher: `on:` with
      `workflow_dispatch` **only** (no `push`, no `pull_request`, no `schedule`),
      `permissions: contents: read`, and exactly two jobs each with a single `uses:` key
      referencing `./.github/workflows/fast-ci.yml` and `./.github/workflows/full-ci.yml`.
      No `run:`, no `steps:`.
      *Validation*: YAML-parsed `on:` has exactly one key; the parsed structure contains two
      jobs, each whose value has a `uses` key and no `steps` key.
      **Result 2026-10-07**: `on:` = `{workflow_dispatch}` only — `push`, `pull_request`,
      `schedule` all confirmed absent. Two jobs `fast` / `full`, each with keys exactly
      `{name, uses}` and **no** `steps`. Comment-stripped scan of the active config finds only
      the two `uses:` lines — no `run:` bodies. Both referenced files exist, and both were
      confirmed to declare `workflow_call` (otherwise the `uses:` would be an invalid
      reference and the dispatcher would never work).
- [X] **T023** [US3] Add a header comment to `ci.yml` stating what it is for, that it runs no
      checks of its own, that it exists to avoid duplicating job definitions across files, and
      — critically — that under manual dispatch the Full workflow's commit-prefix audit is
      skipped because a called workflow observes the caller's `workflow_dispatch` event and the
      audit's gate requires a `push` event.
      *Validation*: the comment names both facts explicitly; a reader can answer "does a green
      manual run mean the prefix audit ran?" without reading `full-ci.yml`.
      **Result 2026-10-07**: 10 documentation points asserted present. The header states:
      what it is for; that it holds no job implementation; that a duplicate copy would drift
      (citing the 139-false-report precedent from `ci.yml.orig:282-288`); that `workflow_call`
      does not cause a double run; that a manual run **skips the commit-prefix audit**; that
      the `if: github.event_name == 'push'` gate was carried over byte-identically and is
      deliberately *not* removed, with the reason (`event.before` is undefined for a manual
      run, so the range would degenerate to a whole-branch scan); and the operational
      conclusion — to actually run the audit, use a push, not this dispatcher. Verified
      answerable in isolation: "稽核沒有跑" appears in `ci.yml` itself.
- [X] **T024** [US3] Confirm the dispatcher introduces no duplicate runs: `ci.yml` has no
      `push`/`pull_request`/`schedule` trigger, and `fast-ci.yml`/`full-ci.yml` do not trigger
      each other.
      *Validation*: assert `ci.yml`'s trigger set is exactly `{workflow_dispatch}`; grep both
      new files and confirm neither references the other's filename in a trigger position.
      **Result 2026-10-07 — trigger matrix**:
      `ci.yml` = dispatch only; `fast-ci.yml` = push + pull_request + dispatch + call;
      `full-ci.yml` = push + schedule + dispatch + call. `ci.yml`'s auto-trigger set is **empty**.
      `workflow_run` appears in **none** of the three files, so there is no chaining mechanism
      that could re-fire anything. Concurrency groups: `fast-ci-${{ github.ref }}` and
      `full-ci-${{ github.ref }}`, confirmed unique.
      Cross-references between the files were checked and are **comment-only** — after
      stripping comments, no `ci.yml` reference survives in either new workflow, so no
      workflow triggers another.
      `push:main` intentionally fires **both** `fast-ci` and `full-ci`: FR-001 and FR-007 each
      require it, they are separate workflows with distinct jobs and distinct concurrency
      groups, so a merge produces one run of each — not a duplicate. Recorded here so the
      coincidence is not later "fixed" by removing one trigger.
      `ci.yml` has no `concurrency` key; acceptable because FR-013 scopes the distinct-group
      requirement to the two executable workflows, and this one is manual-only.

**Checkpoint**: All three stories functional.

---

## Phase 5: Test Alignment (serves US1)

**Purpose**: Keep the one existing test that constrains the compose job's location pointing at
the workflow that now owns that job, without weakening it.

- [X] **T025** [US1] Retarget `tests/test_ci_compose_env.py` to `fast-ci.yml`: change the `CI`
      constant at line 21, the `assert start != -1` message at line 39, and the `ci.yml`
      references in the docstring at lines 1 and 12. Change nothing else — not the
      block-boundary parsing, not the regexes, not the assertions.
      *Validation*: `.venv/bin/pytest -q tests/test_ci_compose_env.py` passes (3 tests); the diff
      touches only the four intended strings.
      **Result 2026-10-07**: 3 passed. Diff inspected line by line — every changed line is a
      filename reference (docstring ×2, `CI` constant, `_compose_job_env` docstring, the
      `assert start != -1` message, and the two failure-message f-strings, which were also
      retargeted so a failure names the file that actually carries the compose job). A
      grep for `def `/`re.`/`find(`/`assert` in the diff returns **only** the message-text
      line — no regex, no parsing, no assertion logic changed. Added a 3-line comment at the
      constant recording the rename date, why the compose job lives in `fast-ci.yml` and not
      `full-ci.yml`, and that only the filename changed.
- [X] **T026** [US1] Prove the retargeted test still has teeth per SC-008: copy
      `compose.yaml` to a temp path, add a `${NEWREQUIRED:?...}` placeholder, confirm the test
      fails against the modified copy, then restore the original.
      **Result 2026-10-07**: mutation appended a service requiring
      `${NEWREQUIRED:?…}` → `1 failed, 2 passed`, with
      `AssertionError: fast-ci.yml 的 compose job 沒給這些必填變數: ['NEWREQUIRED']`. The
      message correctly names **`fast-ci.yml`**, confirming the retarget did not merely
      silence the check. Original restored from backup: 3 passed, and
      `git diff --quiet -- compose.yaml` clean. `compose.yaml` is untouched by this feature.
      *Validation*: the unmodified test passes and the modified-copy run fails with a message
      naming `NEWREQUIRED`; the working tree is clean afterwards (`git diff --quiet`).

---

## Phase 6: Cross-Cutting Validation

- [X] **T027** [P] SC-001: verify no step was lost — assert the union of steps across all three
      workflows equals the 19-row baseline mapping, and that no check step is implemented in two
      files.
      *Validation*: `specs/003-split-ci-fast-full/check-coverage.sh` extended to all three files
      exits 0; its per-row destination output matches the `spec.md` table exactly.
      **Result 2026-10-07 — exit 0, PASS.** `check-coverage.sh` extended to all three files
      (it already was). Full output: 12 Fast fingerprints `ok`, shared toolchain `ok` in both,
      2 Full fingerprints `ok`, test-selection partition `ok`, dispatcher shape `ok`.
      Per-row destination output matches the `spec.md` table exactly. See T013's note for the
      three bugs found and fixed in the check itself, and for the added partition assertion.
- [X] **T028** [P] SC-010: assert every `uses:` across all three workflows is pinned to a
      40-character lowercase hex SHA with a version comment.
      *Validation*: a regex sweep over all three files returns zero unpinned references.
      **Result 2026-10-07**: 13 `uses:` references across all three files, **0 unpinned** —
      every one a 40-char lowercase-hex SHA. All 13 carry a version comment
      (`v7.0.1`, `v7.0.0`, `v10.2.0`, `v6.1.0`), so Dependabot's `github-actions` ecosystem can
      still read them.
- [X] **T029** [P] SC-011: assert no file outside `.github/workflows/` and
      `tests/test_ci_compose_env.py` changed.
      *Validation*: `git diff --name-only <baseline-commit>` lists only the four expected paths;
      `git diff --check` reports no whitespace errors.
      **Result 2026-10-07**: tracked files modified = exactly 2
      (`.github/workflows/ci.yml`, `tests/test_ci_compose_env.py`). New files = 2 workflows +
      7 files under `specs/003-split-ci-fast-full/` (the spec/plan/tasks documents and the
      task artifacts T001-T003 produce). **No production or application code touched**;
      `compose.yaml`, `backend/`, `frontend/`, `scripts/`, `settings/` all unmodified.
      `git diff --check` reports no whitespace errors.
- [X] **T030** SC-007: assert no real age private key path or secret is referenced by any
      workflow.
      *Validation*: grep all three workflows for `keys.txt`, `SOPS_AGE_KEY` pointing outside a
      workflow-generated path, and `secrets.`; zero matches.
      **Result 2026-10-07 — zero violations.** Checked across all three files with comments
      stripped: no `.config/sops/age/keys.txt`, no `$HOME/.config/sops`, no `${{ secrets.* }}`
      context reference, no literal `AGE-SECRET-KEY-1`, no inline `age1…` key. Both
      `SOPS_AGE_KEY_FILE=` targets are `/tmp/agekey.txt`, created by `age-keygen` inside the
      workflow run. All three workflows keep `permissions: contents: read`.
      One false positive caught and corrected: the first pattern was `secrets\.` (dot = any
      char), which matched the repo filename `settings/env/secrets.common.enc.env` five times.
      Tightened to `\$\{\{\s*secrets\.` — the GitHub context form — so filenames are not
      mistaken for secret references.
- [X] **T031** Confirm the full local test suite still passes with no increase in skip count
      attributable to the split.
      *Validation*: `.venv/bin/pytest -q -p no:cacheprovider` yields the same 674 passed /
      115 skipped / 2 deselected as the pre-split baseline, with no new skip reason.

**Checkpoint**: All requirements verified. Ready for review.

      **Result 2026-10-07**: `674 passed, 115 skipped, 2 deselected in 16.43s` —
      identical to the pre-split baseline on all three numbers.
      Skip-reason breakdown confirms no new reason was introduced by the split:
      `node 不在 PATH` ×8, `本機 qdrant 沒跑` ×2, `沒有 node，跑不了前端函式` ×1,
      `本機 stack 不在線` ×1. Every one is a pre-existing 'missing local environment'
      cause (no node / no local qdrant on this machine), not a consequence of moving
      jobs between workflows. Note this run is on the development machine where node is
      absent; in CI `setup-node` provides it, so the node-related skips are 0 there.
      Nothing in `tests/` asserts on `ci.yml`'s content except
      `test_ci_compose_env.py`, which T025 retargeted — its 3 tests pass.
---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Baseline)**: nothing. **Blocks everything** — the baseline is the only record of
  pre-split content.
- **Phase 2 (US1)**: depends on Phase 1. Blocks nothing else; US1 is independently shippable.
- **Phase 3 (US2)**: depends on Phase 1. Independent of US1.
- **Phase 4 (US3)**: depends on US1 **and** US2 both existing — `uses:` cannot reference an
  absent file.
- **Phase 5 (Test alignment)**: depends on the compose job existing in `fast-ci.yml` (T007),
  since retargeting the test at an absent job would fail for the wrong reason.
- **Phase 6 (Cross-cutting)**: depends on all of the above.

### User Story Dependencies

- **US1 (P1)**: after Phase 1. No dependency on US2 or US3.
- **US2 (P2)**: after Phase 1. No dependency on US1.
- **US3 (P3)**: after US1 and US2 — it references both.

### Within Each User Story

- Test-style tasks first, confirmed to fail before the implementation they check.
- Workflow scaffolding (triggers, concurrency) before jobs.
- Job creation before step population.
- Comment porting last, once the step bodies are final.

### Parallel Opportunities

- T005, T006, T007, T008 are independent job constructions within US1.
- T017 and T019 are independent jobs within US2.
- T027, T028, T029, T030 are independent verifications.
- US1 and US2 can be developed fully in parallel by different people.
- T002 must not run concurrently with T001's read of the same file.

---

## Scope Boundary

**In scope** — `.github/workflows/ci.yml` (rewritten to a dispatcher),
`.github/workflows/fast-ci.yml` (new), `.github/workflows/full-ci.yml` (new),
`tests/test_ci_compose_env.py` (four strings).

**Explicitly out of scope** — production or application code; the `age` /
`test_backup_env.py` 10-test skip defect; a code formatter; a linter; commit-signing
verification; branch-policy checks; SOPS/AGE "jobs" as such (no such jobs exist);
`ARCHITECTURE.md` line 45 and lines 165-167 updates; enabling `required_status_checks`.

---

## Validation Strategy

| Layer | Technique | Catches |
|---|---|---|
| Syntax | YAML parse of all three workflows | malformed YAML, wrong trigger shape |
| Structural | Assert trigger sets, job sets, `uses:` pinning, absence of `steps` in the dispatcher | mis-scoped triggers, unpinned actions, a dispatcher that grew jobs |
| Coverage | Baseline table vs union of steps across all three files | a dropped or double-implemented step (SC-001) |
| Behaviour | `check-audit.sh` mode 1 (detection) and mode 2 (no false positives) | an audit that cannot detect, or that false-positives on history |
| Behaviour | `sops filestatus` + round-trip against real and plaintext fixtures | a credential check that cannot fail (SC-006) |
| Contract | Existing `tests/test_ci_compose_env.py` unmodified in substance, plus a mutation proving it still fails | a test silently weakened into vacuity (SC-008) |
| Regression | Full local `pytest` compared against the 674/115/2 baseline | a skip regression from the split (SC-003) |
| Scope | `git diff --name-only` against a baseline commit | unintended collateral edits (SC-011) |
| Timing | Observe Fast's slowest job on the first 10 PRs | the 5-minute target (SC-002) — cannot be verified before the workflows run |

Every check except SC-002 is executable locally before merge. SC-002 is the one criterion
that can only be observed on GitHub, and it is the reason the plan is not complete until the
first ten PRs have run.

---

## Remaining Gaps

Recorded in `spec.md` §Explicit Gaps, not implemented here:

| Gap | State | Needed to close |
|---|---|---|
| Code formatter | None configured anywhere | A maintainer decision on tool and baseline, then a new dependency and a failure surface across all existing code |
| Security linters (as named) | None; existing equivalents are the credential scan and IP policy, already mapped | A maintainer decision on which linters |
| Commit-signing verification | All 374 commits unsigned; no key configured | A baseline decision — rewrite history, or accept unsigned history — then a check scoped to that baseline |
| Branch-policy checks | One ruleset, one rule (`deletion`); no branch protection | Policies to check; adding checks for absent policies would fabricate coverage |
| SOPS/AGE validation jobs (as named) | No such jobs exist | Nothing — the equivalent depth is delivered by the `secrets` job plus existing invariant tests |
| `age` / `test_backup_env.py` skip | 10 tests skip in CI today; `backup-env.sh` calls `age -r` | Installing the full `age` package in the Full `secrets` job; deliberately deferred from this feature |
| `ARCHITECTURE.md` staleness | Lines 45 and 165-167 reference `ci.yml` job names; line 45 says "三 job" (there are four) | A documentation follow-up once the split lands |
| `required_status_checks` enabling | Deliberately omitted per `ARCHITECTURE.md:178` | A decision to require PR checks — which would make job names an external contract and require coordinated renaming |
| SC-002 timing | Cannot be observed before the workflows run on GitHub | The first 10 pull requests |