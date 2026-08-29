#!/usr/bin/env bash
# Single source of truth for the safety-scanner exclude list.
#
# The scanner blocks name/email/phone patterns from being committed (D7 M3).
# Files that legitimately hold digit runs, placeholder email shapes, or the
# scanner's own regex source are excluded:
#   - scripts/precommit_scan.py         — self-match on the regex docstring
#   - tests/test_precommit_scan.py      — deliberate email/phone fixtures
#   - CHANGELOG.md                      — prose describing the scanner
#   - uv.lock                           — sha256 hashes contain 10-15-digit runs
#
# Both CI (.github/workflows/ci.yml) and the pre-commit config
# (.pre-commit-config.yaml, via `entry: bash scripts/scan_tracked.sh`)
# invoke this script so the exclude list cannot drift between them.
# H-precommit-changelog-1 in the A10 review — CI was excluding CHANGELOG.md,
# the local pre-commit hook was not, so `pre-commit run --all-files` would
# fail on a clean tree.
set -euo pipefail

EXCLUDES=(
  --exclude 'tests/test_precommit_scan.py'
  --exclude 'scripts/precommit_scan.py'
  --exclude 'CHANGELOG.md'
  --exclude 'uv.lock'
)

# Collect files into a bash array so we can invoke the scanner exactly once
# and read its explicit exit code. The previous shape was
# `git ls-files -z | xargs -0 python …`, which on some bash builds
# (notably Git Bash for Windows) failed to propagate xargs's non-zero exit
# through `set -euo pipefail`, so a scanner hit would silently report as
# green locally while CI's Linux bash correctly reported red. One
# invocation with an explicit `if` closes that gap.
if [ "$#" -eq 0 ]; then
  # No files passed: scan every tracked file (CI mode).
  mapfile -d '' FILES < <(git ls-files -z)
  set +e
  python scripts/precommit_scan.py "${EXCLUDES[@]}" "${FILES[@]}"
  rc=$?
  set -e
  exit "$rc"
else
  # Files passed (pre-commit mode): scan just those.
  python scripts/precommit_scan.py "${EXCLUDES[@]}" "$@"
fi
