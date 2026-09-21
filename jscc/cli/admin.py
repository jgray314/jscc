"""Setup and read-only commands: validate-config, db init, seed, report, costs."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from pydantic import ValidationError

from ..config import (
    LoadError,
    load_profile,
    load_stages,
    resolve_profile_path,
)
from ..cost_report import find_cost_regressions, format_cost_report, summarize_costs
from ..mode import DEFAULT_DATA_DIR, Mode
from ..report import detect_stale, format_report, funnel_counts
from ..seed import DEFAULT_SEED, seed_synthetic
from ..storage import (
    list_applications,
    list_llm_calls,
    read_mode_marker,
    schema_version,
)
from ._app import cli, db
from ._common import (
    DEFAULT_CONFIG_DIR,
    EXIT_USAGE,
    _open_or_exit,
    _parse_now,
    _resolve_mode_or_exit,
    echo,
)


@cli.command("validate-config")
@click.option(
    "--config-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_CONFIG_DIR,
    show_default=True,
    help="Directory containing stages.yaml and profile.*.yaml.",
)
def validate_config(config_dir: Path) -> None:
    """Validate stages.yaml and the active profile. Exit 0 on success, non-zero on failure."""
    mode = _resolve_mode_or_exit()
    errors: list[str] = []

    stages_path = config_dir / "stages.yaml"
    try:
        load_stages(stages_path)
        echo(f"[OK] {stages_path}")
    except LoadError as e:
        errors.append(f"[FAIL] {stages_path}: {e}")
    except ValidationError as e:
        errors.append(f"[FAIL] {stages_path}: schema errors:\n{e}")

    try:
        profile_path = resolve_profile_path(config_dir, mode=mode)
        load_profile(profile_path)
        echo(f"[OK] {profile_path}")
    except LoadError as e:
        errors.append(f"[FAIL] {config_dir}: {e}")
    except ValidationError as e:
        errors.append(f"[FAIL] profile: schema errors:\n{e}")

    if errors:
        for msg in errors:
            echo(msg, err=True)
        sys.exit(1)
    echo("all configs valid")


@db.command("init")
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_DATA_DIR,
    show_default=True,
    help="Directory holding mode DBs (data/<mode>.db).",
)
def db_init(data_dir: Path) -> None:
    """Create the JSCC schema for the active mode (JSCC_DATA, default synthetic)."""
    mode = _resolve_mode_or_exit()
    conn = _open_or_exit(mode, data_dir)
    try:
        version = schema_version(conn)
        stamped = read_mode_marker(conn)
    finally:
        conn.close()
    echo(
        f"initialized {data_dir}/{mode.value}.db "
        f"(schema v{version}, mode marker: {stamped.value if stamped else 'none'})"
    )


@cli.command("seed")
@click.option(
    "--synthetic",
    "mode_flag",
    flag_value="synthetic",
    default="synthetic",
    help="Load the synthetic fixture. Currently the only supported mode.",
)
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_DATA_DIR,
    show_default=True,
    help="Directory holding mode DBs.",
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
@click.option(
    "--now",
    "now_str",
    type=str,
    default=None,
    help=(
        "Pin the fixture's reference 'now' (UTC ISO-8601, e.g. "
        "2026-08-28T12:00:00+00:00). Required for run-to-run reproducibility "
        "with --random-seed. Defaults to the current UTC time (non-deterministic)."
    ),
)
def seed(
    mode_flag: str,
    data_dir: Path,
    random_seed: int,
    no_reset: bool,
    now_str: str | None,
) -> None:
    """Populate the synthetic DB with a fixture. Refuses to run in real mode.

    With --random-seed alone the RNG choices (companies, stage counts, contact
    names) are reproducible, but the timestamps (`applied_at`, `created_at`,
    `last_interaction_at`) are anchored on the current wall clock. Pass --now
    with an explicit UTC timestamp for full run-to-run reproducibility.
    """
    active_mode = _resolve_mode_or_exit()
    if active_mode is not Mode.synthetic:
        echo(
            f"refusing to seed: JSCC_DATA={active_mode.value}. "
            f"Synthetic seeding is only allowed in synthetic mode.",
            err=True,
        )
        sys.exit(EXIT_USAGE)
    if mode_flag != "synthetic":
        raise click.UsageError(f"unsupported seed mode: {mode_flag}")

    now_pinned = _parse_now(now_str)

    conn = _open_or_exit(active_mode, data_dir)
    try:
        counts = seed_synthetic(
            conn,
            reset=not no_reset,
            random_seed=random_seed,
            now=now_pinned,
        )
    finally:
        conn.close()

    summary = ", ".join(f"{k}={v}" for k, v in counts.items())
    echo(f"seeded {data_dir}/{active_mode.value}.db: {summary}")


@cli.command("report")
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_DATA_DIR,
    show_default=True,
    help="Directory holding mode DBs.",
)
@click.option(
    "--config-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_CONFIG_DIR,
    show_default=True,
    help="Directory containing stages.yaml.",
)
@click.option(
    "--now",
    "now_str",
    type=str,
    default=None,
    help=(
        "Measure staleness against this instant (UTC ISO-8601) instead of the "
        "wall clock. Needed to reproduce the stale block of a pinned seed."
    ),
)
def report(data_dir: Path, config_dir: Path, now_str: str | None) -> None:
    """Print pipeline funnel counts and stale-alert list for the active mode.

    Funnel counts depend only on stored state. The stale block does not -- it is
    measured against `now`, so without `--now` it drifts by a day per day and a
    sample pasted into a README stops matching the day after it was pasted.
    """
    mode = _resolve_mode_or_exit()
    now = _parse_now(now_str)
    stages_path = config_dir / "stages.yaml"
    # This used to call load_stages bare and let a bad
    # config crash with a raw traceback from whatever CWD the user happened
    # to be in, unlike validate-config's try/except around the same call.
    try:
        stages_cfg = load_stages(stages_path)
    except (LoadError, ValidationError) as e:
        raise click.UsageError(f"{stages_path}: {e}") from e
    conn = _open_or_exit(mode, data_dir)
    try:
        apps = list_applications(conn)
    finally:
        conn.close()
    counts = funnel_counts(apps, stages_cfg)
    try:
        alerts = detect_stale(apps, stages_cfg, now=now)
    except ValueError as e:
        # `detect_stale` raises the same ValueError whether
        # the cause is corrupt data (a genuinely unexpected bug -- let it
        # crash) or a --now the caller passed that lands before some app's
        # last-interaction timestamp. Only we know here which one it was:
        # --now exists so a reader can reproduce a pasted sample, and
        # pointing it at the wrong instant is the first mistake anyone makes
        # with it, so treat that case as a usage error rather than a crash.
        if now_str is not None:
            raise click.UsageError(str(e)) from e
        raise
    echo(f"[mode: {mode.value}]")
    echo(format_report(counts, alerts, stages_cfg))


@cli.command("costs")
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_DATA_DIR,
    show_default=True,
    help="Directory holding mode DBs.",
)
def costs(data_dir: Path) -> None:
    """Print per-feature LLM cost/latency summary for the active mode.

    Empty for a mode that has made no LLM calls yet. Calls that failed
    mid-request are counted separately, since they may still have been billed.
    """
    mode = _resolve_mode_or_exit()
    conn = _open_or_exit(mode, data_dir)
    try:
        calls = list_llm_calls(conn)
    finally:
        conn.close()

    echo(f"[mode: {mode.value}]")
    summaries = summarize_costs(calls)
    regressions = find_cost_regressions(calls)
    echo(format_cost_report(summaries, regressions))
