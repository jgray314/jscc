"""Data-mode isolation (D7 M1).

Two mutually-exclusive modes: `synthetic` (default; fake demo fixture) and
`real` (personal, gitignored data). The mode is selected by the `JSCC_DATA`
environment variable; each mode maps to a distinct DB file path. Structural
enforcement — a marker inside the DB itself refuses cross-mode opens — lives
in storage.py under `open_for_mode` / `ModeMismatchError`.
"""
from __future__ import annotations

from enum import Enum
from os import environ
from pathlib import Path

from .paths import PACKAGE_ROOT


ENV_VAR = "JSCC_DATA"


class Mode(str, Enum):
    synthetic = "synthetic"
    real = "real"


class InvalidModeError(ValueError):
    """The JSCC_DATA env var was set to a value that isn't a known mode."""


DEFAULT_MODE = Mode.synthetic

# Anchored to the package, not to the process's working directory.
#
# Rerun-gate finding M-3: this was `Path("data")`, so `JSCC_DATA=real` run from
# any other directory created a fresh, correctly-stamped `real.db` *there* --
# outside the `.gitignore` that is D7 M2, with no warning and a success message.
# The mode marker did its job; the file just wasn't where the protections are.
# Two of D7's seven mitigations (M2's ignore patterns and M3's danger list, see
# H-1) both keyed on the working directory, so one wrong `cd` disabled both at
# once. `--data-dir` still overrides for anyone who means it.
DEFAULT_DATA_DIR = PACKAGE_ROOT / "data"


def resolve_mode(env_value: str | None = None) -> Mode:
    """Resolve the active mode. Prefers explicit `env_value`; otherwise reads
    the `JSCC_DATA` env var; defaults to synthetic when neither is set.
    """
    if env_value is None:
        env_value = environ.get(ENV_VAR)
    if env_value is None or env_value == "":
        return DEFAULT_MODE
    try:
        return Mode(env_value)
    except ValueError as e:
        allowed = ", ".join(m.value for m in Mode)
        raise InvalidModeError(
            f"{ENV_VAR}={env_value!r} is not a valid mode; expected one of: {allowed}"
        ) from e


def resolve_db_path(mode: Mode, data_dir: Path | None = None) -> Path:
    """Path convention: `<data_dir>/<mode>.db`."""
    return (data_dir or DEFAULT_DATA_DIR) / f"{mode.value}.db"
