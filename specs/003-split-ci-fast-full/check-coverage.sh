#!/usr/bin/env bash
# check-coverage.sh — prove the CI split neither dropped a check nor implemented one twice.
#
# Baseline: specs/003-split-ci-fast-full/baseline-steps.txt (19 steps of the pre-split ci.yml,
# captured by T001). Destination table: specs/003-split-ci-fast-full/spec.md.
#
# Every one of the 19 baseline steps must appear in exactly one destination workflow:
#   Fast only : rows 1-6, 8-15, 18, 19   (16 rows)
#   Shared    : row 7  (toolchain install — runs in BOTH fast-ci.yml and full-ci.yml)
#   Full only : rows 16, 17
#
# Row 7 is the only row legitimately present twice, because it is genuinely needed in both
# workflows. It is still counted once in the partition so the 19 stay disjoint.
#
# Usage:  ./check-coverage.sh            # verify all three workflows
#         ./check-coverage.sh --fast     # verify fast-ci.yml alone (used by T013)
set -uo pipefail

SPEC_DIR="$(cd "$(dirname "$0")" && pwd)"
WF_DIR="$SPEC_DIR/../../.github/workflows"
FAST="$WF_DIR/fast-ci.yml"
FULL="$WF_DIR/full-ci.yml"
CI="$WF_DIR/ci.yml"

fail=0
note() { printf '%s\n' "$*"; }
err()  { printf '::error::%s\n' "$*"; fail=1; }

# ── fingerprint: each step is identified by a distinctive, stable string ────────────────
# Kept as explicit literals (not parsed out of spec.md) so this script is itself an
# independent statement of the mapping — if spec.md's table is edited wrongly, this
# still asserts what the mapping was supposed to be.
# Rows that are **checks** (assert something about the repo) vs rows that are **scaffolding**
# (prepare an environment). The no-leak rule below applies only to checks: duplicating
# `setup-uv` / `setup-node` / `uv sync` across two workflows is correct and necessary, because
# the two run on different runners and on different test selections. What must never be
# duplicated is a *check* — that is what drifts.
#
# `.venv/bin/pytest` is listed here as scaffolding rather than a check because it appears in
# both workflows with **different arguments**: fast-ci runs the default selection (789 tests,
# `-m 'not slow'` from pyproject addopts) and full-ci's test-full runs `-o addopts= -m slow`
# (2 tests). Together they partition all 791 — no test executes twice. That partition is
# asserted explicitly below, so "pytest is in both files" is not taken on trust.
declare -A SCAFFOLDING=(
  [1]='astral-sh/setup-uv@'
  [5]='actions/setup-node@'
  [7]='.venv/bin/pytest'
  [2]='uv sync --frozen'
)

declare -A ROW_SIG=(
  [1]='astral-sh/setup-uv@'                # backend: setup-uv
  [2]='uv sync --frozen'                   # backend: install deps
  [3]='ast.parse'                          # backend: python syntax check
  [4]='^LAN_IP='                           # backend: IP policy
  [5]='actions/setup-node@'                # backend: node for the tests
  [6]='age-keygen'                         # backend: sops + age-keygen  (SHARED row)
  [7]='.venv/bin/pytest'                   # backend: pytest
  [8]='pnpm/action-setup@'                 # frontend: pnpm
  [9]='pnpm install --frozen-lockfile'     # frontend: install
  [10]='pnpm run build'                    # frontend: build
  [11]='docker compose config'             # compose: config -q
  [12]='單檔不得超過 10 MB'                 # security: file-size guard
  [13]='cfut_'                             # security: credential scan (pattern block)
)

# Rows needing the guards job specifically, checked only against full-ci.yml.
#
# ⚠️ These two must be matched against **active config only**, not raw text. `HOSTS=` and
# `fetch-depth:` both appear in explanatory comments elsewhere (fast-ci.yml's syntax-check
# comment quotes `HOSTS=`, and so does the original ci.yml's guards comment), and a raw
# grep would call a passing PR gate a violation of "the audit must not be in Fast".
# `strip_comments` removes `#`-comments before matching.
declare -A FULL_ONLY_SIG=(
  [16]='fetch-depth: 0'                    # guards: full-depth checkout
  [17]='HOSTS='                             # guards: commit-prefix audit
)

# Remove YAML comments, leaving code only. `run:` bodies are shell where `#` can also start
# a comment, so stripping inside them is safe here: none of the fingerprints we match live
# after a `#` on the same line.
#
# ⚠️ Do NOT rewrite this as `strip_comments f | grep -q …`. That is the SIGPIPE trap this
# repository already documents at .githooks/pre-push:60-70: `grep -q` exits on the first
# match, the upstream `sed` dies with 141, `pipefail` propagates it, and the caller's
# `&&` reports the row as MISSING even though it is present. It fails intermittently and
# looks like a flaky check. Match into a variable first, then test the variable.
strip_comments() { sed 's/[[:space:]]#.*$//' "$1"; }

matches() {  # $1 = file, $2 = signature. No pipeline into grep -q.
  [ -f "$1" ] || return 1
  local body
  body="$(strip_comments "$1")"          # sed completes here; no SIGPIPE possible
  case "$body" in *"$2"*) return 0 ;; *) return 1 ;; esac
}

note "== step-fingerprint presence =="

# Which workflows must contain each Fast-assigned row.
present_in() { matches "$1" "$2"; }  # active config only (comments stripped)

for row in "${!ROW_SIG[@]}"; do
  sig="${ROW_SIG[$row]}"
  in_fast=no; in_full=no
  present_in "$FAST" "$sig" && in_fast=yes
  present_in "$FULL" "$sig" && in_full=yes

  case "$row" in
    6)  # the sops/age-keygen toolchain — row 7 of spec.md, legitimately in both
      if [ "$in_fast" = no ] || [ "$in_full" = no ]; then
        err "Shared toolchain (sops/age-keygen) must appear in BOTH fast-ci.yml and full-ci.yml (fast=$in_fast full=$in_full)"
      else
        note "  ok  shared toolchain present in both"
      fi
      ;;
    *)
      if [ "$in_fast" = no ]; then
        err "Fast-assigned row missing from fast-ci.yml: '$sig'"
      else
        note "  ok  fast-ci.yml has '$sig'"
      fi
      # A *check* must not be duplicated across workflows — that is the drift risk.
      # Scaffolding legitimately appears in both (separate runners, different selections).
      if [ "${SCAFFOLDING[$row]:-}" = "$sig" ]; then
        [ "$in_full" = yes ] && note "  ok  scaffolding '$sig' in both (expected)"
        continue
      fi
      if [ "$in_full" = yes ]; then
        err "Fast-only CHECK duplicated in full-ci.yml (would drift): '$sig'"
      fi
      ;;
  esac
done

for row in "${!FULL_ONLY_SIG[@]}"; do
  sig="${FULL_ONLY_SIG[$row]}"
  if present_in "$FULL" "$sig"; then
    note "  ok  full-ci.yml has '$sig'"
  else
    err "Full-assigned row missing from full-ci.yml: '$sig'"
  fi
  # The full-history checkout and the prefix audit must NOT be in the PR gate.
  if present_in "$FAST" "$sig"; then
    err "Full-only check must NOT be in fast-ci.yml: '$sig'"
  fi
done

# ── ci.yml must stay a dispatcher ──────────────────────────────────────────────────────
# ── the two test selections must partition the suite, not overlap ─────────────────────
note "== test-selection partition =="
if [ -f "$FAST" ] && [ -f "$FULL" ]; then
  fast_sel="$(strip_comments "$FAST" | grep -oE '\-o addopts= -m slow' | head -1)"
  full_sel="$(strip_comments "$FULL" | grep -oE '\-o addopts= -m slow' | head -1)"
  if [ -n "$fast_sel" ]; then
    err "fast-ci.yml must NOT run the slow selection — that is full-ci.yml's job"
  else
    note "  ok  fast-ci.yml runs the default (non-slow) selection"
  fi
  if [ -n "$full_sel" ]; then
    note "  ok  full-ci.yml runs the slow selection (-o addopts= -m slow)"
  else
    err "full-ci.yml must run the slow selection (-o addopts= -m slow)"
  fi
fi

# ── ci.yml must stay a dispatcher ──────────────────────────────────────────────────────
note "== ci.yml dispatcher shape =="
if [ -f "$CI" ]; then
  # A dispatcher's jobs carry `name:` + `uses:` — that is not a step. The markers of an
  # implemented job are `steps:` and `run:`. Checking for `name:` here would flag a
  # perfectly valid dispatcher, which is what the first version of this check did.
  if grep -qE '^\s+steps:' "$CI" || grep -qE '^\s+run:' "$CI"; then
    err "ci.yml must not contain steps of its own (found a steps:/run: line)"
  else
    note "  ok  ci.yml has no steps: and no run: bodies"
  fi
  # Every job must be a pure reference.
  njobs="$(strip_comments "$CI" | grep -cE '^\s{2}[A-Za-z0-9_-]+:$')"
  nuses="$(strip_comments "$CI" | grep -cE '^\s+uses: \./\.github/workflows/')"
  if [ "$nuses" -ne 2 ]; then
    err "ci.yml should reference exactly 2 workflows via uses:, found $nuses"
  else
    note "  ok  ci.yml references both workflows via uses:"
  fi
  if grep -qE 'fast-ci\.yml|full-ci\.yml' "$CI"; then
    note "  ok  ci.yml references the two workflows"
  else
    err "ci.yml does not reference fast-ci.yml / full-ci.yml"
  fi
  # A push/pull_request/schedule trigger on the dispatcher would create duplicate runs.
  for trig in 'push:' 'pull_request:' 'schedule:'; do
    if grep -qE "^\s+${trig}" "$CI"; then
      err "ci.yml must not declare a '${trig}' trigger (would duplicate runs)"
    fi
  done
  note "  ok  ci.yml declares no push/pull_request/schedule trigger"
else
  err "ci.yml is missing"
fi

# ── verdict ────────────────────────────────────────────────────────────────────────────
if [ "$fail" -eq 0 ]; then
  note ""
  note "PASS — every baseline step has exactly one destination; nothing dropped, nothing duplicated."
  exit 0
fi
note ""
note "FAIL — see the ::error:: lines above."
exit 1