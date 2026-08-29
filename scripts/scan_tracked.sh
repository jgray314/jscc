#!/usr/bin/env bash
# Single source of truth for the safety-scanner exclude list.
#
# The scanner blocks name/email/phone patterns from being committed (D7 M3).
# Three files legitimately hold placeholder personal-data shapes and must be
# skipped: the scanner's own tests, the scanner's own regex source (docstring
# self-match), and the CHANGELOG (A7/A8/A10 entries quote placeholder shapes
# as prose describing the scanner).
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
)

if [ "$#" -eq 0 ]; then
  # No files passed: scan every tracked file (CI mode).
  git ls-files -z | xargs -0 python scripts/precommit_scan.py "${EXCLUDES[@]}"
else
  # Files passed (pre-commit mode): scan just those.
  python scripts/precommit_scan.py "${EXCLUDES[@]}" "$@"
fi
