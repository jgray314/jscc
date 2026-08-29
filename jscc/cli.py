from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import click
from pydantic import ValidationError

from .config import (
    LoadError,
    load_profile,
    load_stages,
    resolve_profile_path,
)
from .evals import format_eval_summary, run_jd_extraction_evals
from .extraction import extract_jd
from .fetcher import fetch_jd
from .mode import DEFAULT_DATA_DIR, InvalidModeError, Mode, resolve_mode
from .models import Application, DLQEntry, Resolution
from .report import detect_stale, format_report, funnel_counts
from .seed import DEFAULT_SEED, seed_synthetic
from .storage import (
    ModeMismatchError,
    create_application,
    create_dlq_entry,
    list_applications,
    list_dlq_entries,
    list_llm_calls,
    open_for_mode,
    read_mode_marker,
    resolve_dlq_entry,
    schema_version,
)

FIRST_STAGE = "identified"


def _company_from_url(url: str) -> str:
    """Placeholder company name until extraction covers it (D9's ExtractedJD
    has no company field yet -- see backlog note in jscc.md)."""
    netloc = urlparse(url).netloc
    return netloc.removeprefix("www.") or url

DEFAULT_CONFIG_DIR = Path("config")


def _resolve_mode_or_exit() -> Mode:
    try:
        return resolve_mode()
    except InvalidModeError as e:
        click.echo(str(e), err=True)
        sys.exit(2)


def _open_or_exit(mode: Mode, data_dir: Path):
    try:
        return open_for_mode(mode, data_dir)
    except ModeMismatchError as e:
        click.echo(str(e), err=True)
        sys.exit(2)


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
    help="Directory containing stages.yaml and profile.*.yaml.",
)
def validate_config(config_dir: Path) -> None:
    """Validate stages.yaml and the active profile. Exit 0 on success, non-zero on failure."""
    errors: list[str] = []

    stages_path = config_dir / "stages.yaml"
    try:
        load_stages(stages_path)
        click.echo(f"[OK] {stages_path}")
    except LoadError as e:
        errors.append(f"[FAIL] {stages_path}: {e}")
    except ValidationError as e:
        errors.append(f"[FAIL] {stages_path}: schema errors:\n{e}")

    try:
        profile_path = resolve_profile_path(config_dir)
        load_profile(profile_path)
        click.echo(f"[OK] {profile_path}")
    except LoadError as e:
        errors.append(f"[FAIL] {config_dir}: {e}")
    except ValidationError as e:
        errors.append(f"[FAIL] profile: schema errors:\n{e}")

    if errors:
        for msg in errors:
            click.echo(msg, err=True)
        sys.exit(1)
    click.echo("all configs valid")


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
    click.echo(
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
        click.echo(
            f"refusing to seed: JSCC_DATA={active_mode.value}. "
            f"Synthetic seeding is only allowed in synthetic mode.",
            err=True,
        )
        sys.exit(2)
    if mode_flag != "synthetic":
        raise click.UsageError(f"unsupported seed mode: {mode_flag}")

    now_pinned: datetime | None = None
    if now_str is not None:
        try:
            now_pinned = datetime.fromisoformat(now_str)
        except ValueError as e:
            raise click.UsageError(f"--now is not a valid ISO-8601 timestamp: {e}")
        if now_pinned.tzinfo is None:
            raise click.UsageError(
                "--now must include a timezone offset (e.g. 2026-08-28T12:00:00+00:00)"
            )

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
    click.echo(f"seeded {data_dir}/{active_mode.value}.db: {summary}")


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
def report(data_dir: Path, config_dir: Path) -> None:
    """Print pipeline funnel counts and stale-alert list for the active mode."""
    mode = _resolve_mode_or_exit()
    stages_cfg = load_stages(config_dir / "stages.yaml")
    conn = _open_or_exit(mode, data_dir)
    try:
        apps = list_applications(conn)
    finally:
        conn.close()
    counts = funnel_counts(apps, stages_cfg)
    alerts = detect_stale(apps, stages_cfg)
    click.echo(f"[mode: {mode.value}]")
    click.echo(format_report(counts, alerts, stages_cfg))


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

    Empty until Phase B's first `@instrumented` call lands (D5) — this ledger
    exists ahead of that call on purpose, so nothing is uninstrumented from day one.
    """
    mode = _resolve_mode_or_exit()
    conn = _open_or_exit(mode, data_dir)
    try:
        calls = list_llm_calls(conn)
    finally:
        conn.close()

    click.echo(f"[mode: {mode.value}]")
    if not calls:
        click.echo("no LLM calls recorded yet")
        return

    by_feature: dict[str, list] = {}
    for call in calls:
        by_feature.setdefault(call.feature, []).append(call)

    click.echo(f"{'feature':<20}{'calls':>8}{'cost_usd':>12}{'avg_latency_ms':>16}")
    for feature, feature_calls in sorted(by_feature.items()):
        total_cost = sum(c.cost_usd for c in feature_calls)
        avg_latency = sum(c.latency_ms for c in feature_calls) / len(feature_calls)
        click.echo(
            f"{feature:<20}{len(feature_calls):>8}{total_cost:>12.4f}{avg_latency:>16.1f}"
        )


@cli.group("eval")
def eval_group() -> None:
    """Run an eval suite."""


@eval_group.command("jd_extraction")
def eval_jd_extraction() -> None:
    """Run the JD-extraction eval suite against the current `extract_jd`.

    Exits non-zero if any case fails, so it can gate CI once Slice B2 lands
    a real prompt. Expected to fail every case until then (Slice B1 stub).
    """
    summary = run_jd_extraction_evals(extract_jd)
    click.echo(format_eval_summary(summary))
    if summary.passed < summary.total:
        sys.exit(1)


@cli.command("ingest")
@click.option("--url", required=True, help="Job posting URL to fetch and ingest.")
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_DATA_DIR,
    show_default=True,
    help="Directory holding mode DBs.",
)
def ingest(url: str, data_dir: Path) -> None:
    """Fetch a JD by URL, extract structured fields, and create an Application.

    Never crashes on a bad fetch. Paywalled, blocked, timed-out, or
    unextractable pages land in the DLQ instead (per D6) -- see `dlq list`
    and `resolve-dlq`.
    """
    mode = _resolve_mode_or_exit()
    conn = _open_or_exit(mode, data_dir)
    try:
        result = fetch_jd(url)
        if not result.ok:
            entry = DLQEntry(
                source_url=url,
                failure_mode=result.failure_mode,
                error_detail=result.error_detail,
            )
            entry_id = create_dlq_entry(conn, entry)
            click.echo(
                f"fetch failed ({result.failure_mode.value}); added to DLQ ({entry_id})"
            )
            return

        extracted = extract_jd(result.raw_text, conn=conn)
        app = Application(
            source_url=url,
            source_raw=result.raw_text,
            title=extracted.title or result.title or "(untitled)",
            company=_company_from_url(url),
            stage=FIRST_STAGE,
        )
        app_id = create_application(conn, app)
        click.echo(f"created application {app_id}: {app.title}")
    finally:
        conn.close()


@cli.group("dlq")
def dlq_group() -> None:
    """Dead-letter queue for JDs that failed to fetch or extract cleanly."""


@dlq_group.command("list")
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_DATA_DIR,
    show_default=True,
    help="Directory holding mode DBs.",
)
@click.option(
    "--all", "show_all", is_flag=True, help="Include already-resolved entries."
)
def dlq_list(data_dir: Path, show_all: bool) -> None:
    """List DLQ entries for the active mode (unresolved-only by default)."""
    mode = _resolve_mode_or_exit()
    conn = _open_or_exit(mode, data_dir)
    try:
        entries = list_dlq_entries(conn, unresolved_only=not show_all)
    finally:
        conn.close()

    click.echo(f"[mode: {mode.value}]")
    if not entries:
        click.echo("no unresolved dlq entries")
        return

    click.echo(f"{'id':<38}{'failure_mode':<20}{'source_url'}")
    for entry in entries:
        click.echo(f"{entry.id:<38}{entry.failure_mode.value:<20}{entry.source_url}")


@cli.command("resolve-dlq")
@click.argument("entry_id")
@click.option(
    "--paste-text",
    required=True,
    help="Pasted JD text to use in place of the failed fetch.",
)
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_DATA_DIR,
    show_default=True,
    help="Directory holding mode DBs.",
)
def resolve_dlq(entry_id: str, paste_text: str, data_dir: Path) -> None:
    """Resolve a DLQ entry by pasting the JD text manually (D6 escape hatch).

    Creates the Application the original fetch couldn't, then marks the
    entry resolved. Same code path Slice B4's `ingest --paste` reuses.
    """
    mode = _resolve_mode_or_exit()
    conn = _open_or_exit(mode, data_dir)
    try:
        entries = list_dlq_entries(conn, unresolved_only=False)
        entry = next((e for e in entries if e.id == entry_id), None)
        if entry is None:
            click.echo(f"no DLQ entry with id {entry_id}", err=True)
            sys.exit(2)

        extracted = extract_jd(paste_text, conn=conn)
        app = Application(
            source_url=entry.source_url,
            source_raw=paste_text,
            title=extracted.title or "(untitled)",
            company=_company_from_url(entry.source_url),
            stage=FIRST_STAGE,
        )
        app_id = create_application(conn, app)
        resolve_dlq_entry(conn, entry_id, Resolution.manual_paste)
        click.echo(f"created application {app_id} from DLQ entry {entry_id}")
    finally:
        conn.close()


def main() -> None:
    cli()
