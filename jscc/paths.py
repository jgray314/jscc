"""One definition of where this installation's files live.

Rerun-gate findings H-1 and M-3 were the same bug in two files: the danger
list and the data directory were both declared as working-directory-relative
paths, so running the CLI from anywhere but the repo root silently disabled
D7 M3 (name redaction) and moved the DB outside the ignore rules that are
D7 M2. Neither failure announced itself; both looked like success.

Anchoring is therefore not a detail of any one module, and it is not left to
each module to remember -- the C1 fix already established that a rule enforced
in two places by two copies drifts. `PACKAGE_ROOT` is that rule for paths:
resolved once, from the location of the installed package, never from the
process's working directory.

Callers still take explicit overrides (`--data-dir`, `--config-dir`,
`JSCC_SAFETY_DIR`) -- those are a user saying where to look, which is a
different thing from a default that quietly depends on where they happened
to be standing.
"""
from __future__ import annotations

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
