#!/usr/bin/env bash
# Single source of truth for the safety-scanner exclude list.
#
# The scanner blocks email/phone patterns, Anthropic API keys, and danger-list
# literals from being committed (D7 M3). There is no name *pattern* -- names are
# covered by literals on the danger lists, not by a regex.
# Files that legitimately hold digit runs, placeholder email shapes, or the
# scanner's own regex source are excluded:
#   - scripts/precommit_scan.py         — self-match on the regex docstring
#   - jscc/personal_data.py             — holds the patterns themselves (same
#                                         self-match reason); shared by the
#                                         scanner (M3) and the sanitizer (M5)
#   - tests/test_personal_data.py       — deliberate email/phone fixtures
#   - tests/test_precommit_scan.py      — deliberate email/phone fixtures
#   - CHANGELOG.md                      — prose describing the scanner
#   - uv.lock                           — sha256 hashes contain 10-15-digit runs
#
# evals/jd_extraction/recorded.json used to be excluded here too (same
# hash-looks-like-a-phone-number class as uv.lock), but that also exempted
# its values -- real model output -- from scanning, which the exclusion was
# never meant to cover (gate finding L-14). Its keys are now prefixed
# `sha256:`, and `precommit_scan.py`'s `_SHA256_KEY_RE` strips exactly that
# shape before matching, so the file needs no exclusion and its values are
# scanned like everything else.
# Both CI (.github/workflows/ci.yml) and the pre-commit config
# (.pre-commit-config.yaml, via `entry: bash scripts/scan_tracked.sh`)
# invoke this script so the exclude list cannot drift between them.
# Two copies of the list means one of them excludes a file the other scans,
# and `pre-commit run --all-files` fails on a tree CI accepts.
set -euo pipefail

EXCLUDES=(
  --exclude 'tests/test_precommit_scan.py'
  --exclude 'tests/test_personal_data.py'
  --exclude 'scripts/precommit_scan.py'
  --exclude 'jscc/personal_data.py'
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
