"""Helpers and the exit-code contract shared by every command module."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import click

from ..mode import InvalidModeError, Mode, resolve_mode
from ..paths import PACKAGE_ROOT
from ..storage import (
    ModeMismatchError,
    open_for_mode,
)
from ..terminal import printable

FIRST_STAGE = "identified"

# Anchored like DEFAULT_DATA_DIR: config lives with the package, not wherever
# the process happened to start.
DEFAULT_CONFIG_DIR = PACKAGE_ROOT / "config"


def _resolve_mode_or_exit() -> Mode:
    try:
        return resolve_mode()
    except InvalidModeError as e:
        echo(str(e), err=True)
        sys.exit(EXIT_USAGE)


def _open_or_exit(mode: Mode, data_dir: Path):
    try:
        return open_for_mode(mode, data_dir)
    except ModeMismatchError as e:
        echo(str(e), err=True)
        sys.exit(EXIT_USAGE)


# Exit codes are a contract, not an afterthought. D6 treats a failed fetch as
# an expected product state rather than an error -- the DLQ *is* the feature --
# so a run that queues work is not the same outcome as a run that broke, and a
# script looping over URLs has to be able to tell them apart. Folding both into
# 1 erases exactly the distinction the queue exists to make; leaving the queued
# case at 0 tells a caller that an Application was created when none was.
#
# Gate finding M-9: EXIT_OK on a record-producing command means "the record
# this command is about is in the state it should be" -- not "this specific
# invocation was the one that produced it". `resolve-dlq` against an
# already-resolved entry exits 0 for the same reason `db init` against an
# already-initialized DB does: nothing was wrong, there was just nothing
# left to do. A caller that needs to know whether *this run* did the work
# has that in the output text ("already resolved" vs. "created application
# <id>"), not the exit code -- the code answers "did this leave the system
# in a good state", the message answers "what happened this time".
EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_USAGE = 2
EXIT_QUEUED = 3


def _parse_now(now_str: str | None) -> datetime | None:
    """Parse a pinned `--now`, or None to mean the wall clock.

    Shared by `seed` and `report` deliberately. A fixture pinned to one instant
    and a report read at another produce output that cannot be compared, and
    the two commands disagreeing on the flag's format is the easy way to end up
    with exactly that.
    """
    if now_str is None:
        return None
    try:
        parsed = datetime.fromisoformat(now_str)
    except ValueError as e:
        raise click.UsageError(f"--now is not a valid ISO-8601 timestamp: {e}") from e
    if parsed.tzinfo is None:
        raise click.UsageError(
            "--now must include a timezone offset (e.g. 2026-08-28T12:00:00+00:00)"
        )
    return parsed


def echo(message: object = "", *, err: bool = False) -> None:
    """`click.echo` with control characters removed.

    Every command prints through this. Model output, posting text and exception
    messages that quote them can all carry a terminal escape sequence.
    """
    click.echo(printable(str(message)), err=err)
