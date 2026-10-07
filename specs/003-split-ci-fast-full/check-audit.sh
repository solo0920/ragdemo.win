#!/usr/bin/env bash
# check-audit.sh — prove the commit-prefix audit both DETECTS a bad prefix and does not
# FALSE-POSITIVE on pre-rule history.
#
# Why this exists: plan.md R-1 established that on a shallow clone the audit's boundary
# lookup (`git log --diff-filter=A -- .githooks/commit-msg`) silently returns HEAD instead of
# the real add-commit, so `merge-base --is-ancestor "$sha" "$rules_since"` succeeds for every
# commit and the guard reports NOTHING. It degrades to permanently green, not to visibly broken.
# That is the reason the audit lives in full-ci.yml with fetch-depth: 0 — and it means the
# audit's behaviour must be proven by execution, not assumed from reading YAML.
#
# Modes:
#   detect  — given a full-history clone whose tip has an invalid prefix, assert it IS reported
#   clean   — run the audit in full-branch mode against real history, assert violations=0 and
#             that pre-rule commits were correctly excluded as history
#
# The audit body below is copied verbatim from ci.yml.orig (lines 297-351) including both
# boundary lookups. Do not "improve" it: it has already been repaired twice (2026-10-01, and
# the 139-report regression documented at ci.yml.orig:282-288).
#
# Usage:  ./check-audit.sh detect [repo-path]
#         ./check-audit.sh clean  [repo-path]
set -uo pipefail

MODE="${1:?need a mode: detect|clean}"
SRC="${2:-$(git rev-parse --show-toplevel)}"

# ── the audit, verbatim ───────────────────────────────────────────────────────────────
audit() {
  cd "$SRC" || return 2
  hosts_raw="$(grep -m1 -E '^HOSTS=' settings/env/hosts.shared.env 2>/dev/null \
                | cut -d= -f2- | tr ',' '|' | sed 's/[[:space:]]//g')" || true
  AFTER="$(git rev-parse HEAD)"
  range="$AFTER"
  new_since="$(git log -1 --format='%H' --pickaxe-regex \
                 -S'^HOSTS=.*\bmsi\b' -- settings/env/hosts.shared.env)"
  rules_since="$(git log --diff-filter=A -1 --format='%H' -- .githooks/commit-msg)"
  echo "hosts_raw   = ${hosts_raw:-EMPTY}"
  echo "new_since   = ${new_since:-EMPTY}"
  echo "rules_since = ${rules_since:-EMPTY}"
  if [ "$rules_since" = "$AFTER" ]; then
    echo "SHALLOW-CLONE-SIGNATURE: rules_since resolved to HEAD (full history unavailable)"
  fi
  bad=0; total=0; hist=0
  for sha in $(git rev-list "$range"); do
    total=$((total+1))
    subj="$(git log -1 --format=%s "$sha")"
    if printf '%s' "$subj" | grep -Eq "^($hosts_raw)(:|：)[[:space:]]" \
       || printf '%s' "$subj" | grep -Eq '^(Merge|Revert)'; then
      continue
    fi
    if git merge-base --is-ancestor "$sha" "$rules_since" 2>/dev/null \
       || git merge-base --is-ancestor "$sha" "$new_since" 2>/dev/null; then
      hist=$((hist+1)); continue
    fi
    echo "VIOLATION: ${sha:0:8}  $subj"
    bad=$((bad+1))
  done
  echo "RESULT scanned=$total skipped_as_history=$hist violations=$bad"
}

out="$(audit 2>&1)"; rc=$?
printf '%s\n' "$out"
[ $rc -ne 0 ] && { echo "::error::audit could not run (exit $rc)"; exit 1; }

case "$MODE" in
  detect)
    if printf '%s' "$out" | grep -q '^VIOLATION:'; then
      echo "PASS — invalid prefix detected."
      exit 0
    fi
    echo "::error::audit did NOT report the invalid prefix — it would be a silent false negative"
    exit 1
    ;;
  clean)
    v="$(printf '%s' "$out" | sed -n 's/.*violations=\([0-9]*\).*/\1/p')"
    h="$(printf '%s' "$out" | sed -n 's/.*skipped_as_history=\([0-9]*\).*/\1/p')"
    bad=0
    if [ "${v:-x}" != "0" ]; then
      echo "::error::expected violations=0 on real history, got ${v:-none} — false positives"
      bad=1
    fi
    if [ "${h:-0}" -lt 1 ] 2>/dev/null; then
      echo "::error::expected pre-rule commits to be excluded as history (skipped_as_history>0)"
      bad=1
    fi
    if printf '%s' "$out" | grep -q 'SHALLOW-CLONE-SIGNATURE'; then
      echo "::error::ran against shallow history — the audit is meaningless there (R-1)"
      bad=1
    fi
    [ $bad -eq 0 ] && echo "PASS — 0 violations, pre-rule history correctly excluded."
    exit $bad
    ;;
  *)
    echo "unknown mode: $MODE"; exit 2
    ;;
esac