"""One definition of where this installation's files live.

A working-directory-relative default is a quiet failure waiting to happen:
the danger list loads no terms and the real DB lands outside the ignore rules
that protect it, both without complaint, because everything still *runs*.

Anchoring is therefore not a detail of any one module, and not left to each
module to remember -- a rule enforced in several places by several copies
drifts. `PACKAGE_ROOT` is that rule for paths: resolved once, from the
location of the installed package, never from the process's working
directory.

Callers still take explicit overrides (`--data-dir`, `--config-dir`,
`JSCC_SAFETY_DIR`) -- those are a user saying where to look, which is a
different thing from a default that quietly depends on where they happened
to be standing.
"""
from __future__ import annotations

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
