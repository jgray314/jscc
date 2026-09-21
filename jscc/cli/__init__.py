"""JSCC command line, split by command family.

`_app` holds the root group; importing each command module registers its
commands onto it.
"""

from . import admin, agents, eval_cmds, ingest  # noqa: F401  (registration)
from ._app import cli
from ._common import EXIT_OK, EXIT_QUEUED, EXIT_UNEXPECTED, EXIT_USAGE

__all__ = ["cli", "main", "EXIT_OK", "EXIT_QUEUED", "EXIT_UNEXPECTED", "EXIT_USAGE"]


def main() -> None:
    cli()
