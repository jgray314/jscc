"""The root click group. Command modules register onto it; `jscc.cli` imports them all."""

import click


@click.group()
def cli() -> None:
    """JSCC command line.

    Exit codes for the commands that create records (`ingest`, `resolve-dlq`):

      0  the record was created
      3  handled failure -- a DLQ entry was written; nothing is lost, retry
         with `resolve-dlq`
      2  usage or configuration error; nothing was attempted
      1  unexpected

    The check commands (`validate-config`, `eval`) use the conventional 0/1
    for pass/fail. Splitting the two conventions is deliberate: "did the check
    pass" and "what happened to the work" are different questions, and a
    single scale would have to answer both badly.
    """


@cli.group()
def db() -> None:
    """Database management."""
