from __future__ import annotations

import sys
from pathlib import Path

import click
from pydantic import ValidationError

from .config import LoadError, load_profile, load_stages
from .report import detect_stale, format_report, funnel_counts
from .seed import DEFAULT_SEED, seed_synthetic
from .storage import connect, init_db, list_applications, schema_version

DEFAULT_CONFIG_DIR = Path("config")
# D7 M7-aligned: default to synthetic. A4.5 will formalize a two-instance
# selector; until then, plain synthetic is the only supported target.
DEFAULT_DB_PATH = Path("data/synthetic.db")


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


@cli.command("seed")
@click.option(
    "--synthetic",
    "mode",
    flag_value="synthetic",
    default="synthetic",
    help="Load the synthetic fixture. Currently the only supported mode.",
)
@click.option(
    "--db-path",
    type=click.Path(path_type=Path),
    default=DEFAULT_DB_PATH,
    show_default=True,
    help="SQLite file to seed.",
)
@click.option(
    "--random-seed",
    type=int,
    default=DEFAULT_SEED,
    show_default=True,
    help="Deterministic RNG seed for the fixture.",
)
@click.option(
    "--no-reset",
    is_flag=True,
    default=False,
    help="Append to existing data instead of clearing tables first.",
)
def seed(mode: str, db_path: Path, random_seed: int, no_reset: bool) -> None:
    """Populate the DB with a synthetic fixture (25 applications + contacts + interactions + DLQ)."""
    if mode != "synthetic":  # defensive; click enforces value
        raise click.UsageError(f"unsupported seed mode: {mode}")
    with connect(db_path) as conn:
        init_db(conn)
        counts = seed_synthetic(conn, reset=not no_reset, random_seed=random_seed)
    summary = ", ".join(f"{k}={v}" for k, v in counts.items())
    click.echo(f"seeded {db_path}: {summary}")


@cli.command("report")
@click.option(
    "--db-path",
    type=click.Path(path_type=Path),
    default=DEFAULT_DB_PATH,
    show_default=True,
    help="SQLite file to read from.",
)
@click.option(
    "--config-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_CONFIG_DIR,
    show_default=True,
    help="Directory containing stages.yaml.",
)
def report(db_path: Path, config_dir: Path) -> None:
    """Print pipeline funnel counts and stale-alert list."""
    stages_cfg = load_stages(config_dir / "stages.yaml")
    with connect(db_path) as conn:
        apps = list_applications(conn)
    counts = funnel_counts(apps, stages_cfg)
    alerts = detect_stale(apps, stages_cfg)
    click.echo(format_report(counts, alerts, stages_cfg))


def main() -> None:
    cli()
