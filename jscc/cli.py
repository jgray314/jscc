from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import click
from pydantic import ValidationError

from .config import (
    LoadError,
    load_pipeline,
    load_profile,
    load_stages,
    resolve_profile_path,
)
from .evals import (
    PASS_THRESHOLD,
    RecordingClient,
    ReplayClient,
    format_eval_summary,
    load_recording,
    run_jd_extraction_evals,
    save_recording,
)
from .extraction import EXTRACTION_EVAL_FEATURE, ExtractionParseError, extract_jd
from .fetcher import fetch_jd
from .llm_client import UnknownModelPricingError, default_client
from .mode import DEFAULT_DATA_DIR, InvalidModeError, Mode, resolve_mode
from .models import Application, DLQEntry, FailureMode, Resolution
from .paths import PACKAGE_ROOT
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

# Anchored like DEFAULT_DATA_DIR: config lives with the package, not wherever
# the process happened to start.
DEFAULT_CONFIG_DIR = PACKAGE_ROOT / "config"


def _resolve_mode_or_exit() -> Mode:
    try:
        return resolve_mode()
    except InvalidModeError as e:
        click.echo(str(e), err=True)
        sys.exit(EXIT_USAGE)


def _open_or_exit(mode: Mode, data_dir: Path):
    try:
        return open_for_mode(mode, data_dir)
    except ModeMismatchError as e:
        click.echo(str(e), err=True)
        sys.exit(EXIT_USAGE)


# Exit codes are a contract, not an afterthought. D6 treats a failed fetch as
# an expected product state rather than an error -- the DLQ *is* the feature --
# so a run that queues work is not the same outcome as a run that broke, and a
# script looping over URLs has to be able to tell them apart. Folding both into
# 1 erases exactly the distinction the queue exists to make; leaving the queued
# case at 0 tells a caller that an Application was created when none was.
EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_USAGE = 2
EXIT_QUEUED = 3


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
        sys.exit(EXIT_USAGE)
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
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_DATA_DIR,
    show_default=True,
    help="Directory holding mode DBs. Eval runs are metered to the llm_calls ledger.",
)
@click.option(
    "--record",
    "record",
    is_flag=True,
    default=False,
    help="Capture each live response to evals/jd_extraction/recorded.json.",
)
@click.option(
    "--replay",
    "replay",
    is_flag=True,
    default=False,
    help="Serve recorded responses instead of calling the model. No key, no spend.",
)
@click.option(
    "--min-pass-rate",
    type=float,
    default=PASS_THRESHOLD,
    show_default=True,
    help="Fail below this pass rate.",
)
def eval_jd_extraction(
    data_dir: Path, record: bool, replay: bool, min_pass_rate: float
) -> None:
    """Run the JD-extraction eval suite against the current `extract_jd`.

    Exits non-zero if any case fails, so it can gate CI once B2b lands an
    iterated prompt. Expected to fail every case against the stub client.

    Calls are recorded to the `llm_calls` ledger under the `extraction_eval`
    feature (D5), separate from production `extraction` traffic so prompt
    iteration shows up in `jscc costs` without inflating per-application
    cost.
    """
    if record and replay:
        raise click.UsageError("--record and --replay are mutually exclusive")

    client = None
    if replay:
        recorded = load_recording()
        if not recorded:
            click.echo(
                "no recordings yet; run once with --record against a live key", err=True
            )
            sys.exit(EXIT_USAGE)
        client = ReplayClient(recorded)
    elif record:
        client = RecordingClient(default_client())

    mode = _resolve_mode_or_exit()
    conn = _open_or_exit(mode, data_dir)
    try:
        summary = run_jd_extraction_evals(
            lambda raw: extract_jd(
                raw, conn=conn, client=client, feature=EXTRACTION_EVAL_FEATURE
            )
        )
    finally:
        conn.close()

    if record and client is not None:
        save_recording(client.captured)
        click.echo(f"recorded {len(client.captured)} responses")

    click.echo(format_eval_summary(summary))
    if summary.pass_rate < min_pass_rate:
        click.echo(
            f"pass rate {summary.pass_rate:.0%} is below the {min_pass_rate:.0%} bar",
            err=True,
        )
        sys.exit(1)


# A DLQ entry needs a source_url (NOT NULL), and a pasted JD has none. The
# sentinel keeps the paste path's failures visible in `dlq list` rather than
# silently unrecoverable; `resolve-dlq` recognises it and skips URL-derived
# company inference. Same "(pasted)" spelling the company default already uses.
PASTED_SOURCE = "(pasted)"


def _extract_and_create_application(
    conn,
    *,
    raw_text: str,
    source_url: str | None,
    company: str,
    fallback_title: str | None,
) -> tuple[str, Application]:
    """Shared extract-then-store path for both `ingest` (URL and --paste) and
    `resolve-dlq` -- the DoD for Slice B4 requires the paste path produce the
    same Application shape as the URL path, so both funnel through here."""
    extracted = extract_jd(raw_text, conn=conn)
    app = Application(
        source_url=source_url,
        source_raw=raw_text,
        title=extracted.title or fallback_title or "(untitled)",
        company=company,
        # The whole extraction, not just the field the title comes from: D9
        # splits extract from score because the intermediate output has
        # independent product value, and caching it needs it stored.
        extracted_jd=extracted.model_dump(),
        stage=FIRST_STAGE,
    )
    app_id = create_application(conn, app)
    return app_id, app


@cli.command("ingest")
@click.option("--url", default=None, help="Job posting URL to fetch and ingest.")
@click.option(
    "--paste",
    is_flag=True,
    default=False,
    help="Read JD text from stdin (or --file) instead of fetching a URL.",
)
@click.option(
    "--file",
    "paste_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Read pasted JD text from this file instead of stdin. Implies --paste.",
)
@click.option(
    "--company",
    default=None,
    help="Company name for a pasted JD (no URL to infer it from). Defaults to '(pasted)'.",
)
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
    help="Directory containing pipeline.yaml.",
)
def ingest(
    url: str | None,
    paste: bool,
    paste_file: Path | None,
    company: str | None,
    data_dir: Path,
    config_dir: Path,
) -> None:
    """Fetch a JD by URL, extract structured fields, and create an Application.

    Never crashes on a bad fetch. Paywalled, blocked, timed-out, or
    unextractable pages land in the DLQ instead (per D6) -- see `dlq list`
    and `resolve-dlq`. If a page looks JS-required (thin extracted content)
    and `playwright_fallback: true` is set in pipeline.yaml, retries with a
    rendered browser page before giving up.

    `--paste` (optionally with `--file`) is the escape hatch for any site the
    fetcher can't crack at all -- no URL, no fetch, no DLQ, just pasted JD
    text straight to extraction and storage.
    """
    if url and (paste or paste_file):
        raise click.UsageError("--url and --paste/--file are mutually exclusive")
    if not url and not paste and not paste_file:
        raise click.UsageError("provide --url or --paste (optionally with --file)")

    mode = _resolve_mode_or_exit()
    conn = _open_or_exit(mode, data_dir)
    try:
        if url:
            pipeline_cfg = load_pipeline(config_dir / "pipeline.yaml")
            result = fetch_jd(url, use_playwright_fallback=pipeline_cfg.playwright_fallback)
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
                sys.exit(EXIT_QUEUED)
            raw_text = result.raw_text
            source_url: str | None = url
            company_val = company or _company_from_url(url)
            fallback_title = result.title
        else:
            raw_text = paste_file.read_text(encoding="utf-8") if paste_file else sys.stdin.read()
            if not raw_text.strip():
                click.echo("no JD text provided (empty stdin/file)", err=True)
                sys.exit(EXIT_USAGE)
            source_url = None
            company_val = company or "(pasted)"
            fallback_title = None

        try:
            app_id, app = _extract_and_create_application(
                conn,
                raw_text=raw_text,
                source_url=source_url,
                company=company_val,
                fallback_title=fallback_title,
            )
        except ExtractionParseError as e:
            # A model that wraps its JSON in prose or a ``` fence is the
            # normal case, not an exotic one. "Produces an Application or a
            # DLQEntry, never crashes" covers this stage too, not just fetch.
            entry = DLQEntry(
                source_url=source_url or PASTED_SOURCE,
                failure_mode=FailureMode.extraction_failed,
                error_detail=str(e),
            )
            entry_id = create_dlq_entry(conn, entry)
            click.echo(f"extraction failed; added to DLQ ({entry_id})")
            click.echo(f"  {e}", err=True)
            sys.exit(EXIT_QUEUED)
        except UnknownModelPricingError as e:
            # Deliberately *not* DLQ'd, unlike the reviewer's suggested shape.
            # An unpriced model is a misconfiguration, not a bad JD: every
            # ingest would fail identically, so filling the queue with entries
            # that re-fail on resolve would bury the one thing worth reading.
            # Nothing was billed either -- the check runs before the request.
            click.echo(f"configuration error: {e}", err=True)
            sys.exit(EXIT_USAGE)
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
            sys.exit(EXIT_USAGE)

        company = (
            "(pasted)"
            if entry.source_url == PASTED_SOURCE
            else _company_from_url(entry.source_url)
        )
        try:
            app_id, _app = _extract_and_create_application(
                conn,
                raw_text=paste_text,
                source_url=None if entry.source_url == PASTED_SOURCE else entry.source_url,
                company=company,
                fallback_title=None,
            )
        except ExtractionParseError as e:
            # No new DLQ entry here -- one already exists and stays unresolved,
            # which is the correct record. Creating a second would duplicate the
            # queue on every retry.
            click.echo(f"extraction failed; DLQ entry {entry_id} left unresolved", err=True)
            click.echo(f"  {e}", err=True)
            sys.exit(EXIT_QUEUED)
        resolve_dlq_entry(conn, entry_id, Resolution.manual_paste)
        click.echo(f"created application {app_id} from DLQ entry {entry_id}")
    finally:
        conn.close()


def main() -> None:
    cli()
