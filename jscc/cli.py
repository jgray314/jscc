from __future__ import annotations

import sys
from pathlib import Path

import click
from pydantic import ValidationError

from .config import LoadError, load_profile, load_stages
from .storage import connect, init_db, schema_version

DEFAULT_CONFIG_DIR = Path("config")
DEFAULT_DB_PATH = Path("data/dev.db")


@click.group()
def cli() -> None:
    """JSCC command line."""


@cli.group()
def db() -> None:
    """Database management."""


@cli.command("validate-config")
@click.option(
    "--config-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_CONFIG_DIR,
    show_default=True,
    help="Directory containing stages.yaml and profile.yaml.",
)
def validate_config(config_dir: Path) -> None:
    """Validate stages.yaml and profile.yaml. Exit 0 on success, non-zero on failure."""
    errors: list[str] = []
    for name, loader in (("stages.yaml", load_stages), ("profile.yaml", load_profile)):
        path = config_dir / name
        try:
            loader(path)
            click.echo(f"[OK] {path}")
        except LoadError as e:
            errors.append(f"[FAIL] {path}: {e}")
        except ValidationError as e:
            errors.append(f"[FAIL] {path}: schema errors:\n{e}")

    if errors:
        for msg in errors:
            click.echo(msg, err=True)
        sys.exit(1)
    click.echo("all configs valid")


@db.command("init")
@click.option(
    "--db-path",
    type=click.Path(path_type=Path),
    default=DEFAULT_DB_PATH,
    show_default=True,
    help="SQLite file path.",
)
def db_init(db_path: Path) -> None:
    """Create the JSCC schema at db-path (idempotent)."""
    with connect(db_path) as conn:
        init_db(conn)
        version = schema_version(conn)
    click.echo(f"initialized {db_path} at schema version {version}")


def main() -> None:
    cli()
