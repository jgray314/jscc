from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import anthropic
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
from .models import Application, DLQEntry, FailureMode, FetchStatus, Resolution
from .paths import PACKAGE_ROOT
from .report import detect_stale, format_report, funnel_counts
from .seed import DEFAULT_SEED, seed_synthetic
from .storage import (
    ModeMismatchError,
    create_application,
    create_dlq_entry,
    get_application,
    list_applications,
    list_dlq_entries,
    list_llm_calls,
    open_for_mode,
    read_mode_marker,
    resolve_dlq_entry,
    schema_version,
    update_application,
)

FIRST_STAGE = "identified"


def _company_from_url(url: str) -> str:
    """Fallback company name for when extraction doesn't find one -- an
    ATS page whose JD text never names the employer, or the stub client.
    `_extract_and_create_application` prefers `ExtractedJD.company` over
    this whenever extraction returns one."""
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
        raise click.UsageError(f"--now is not a valid ISO-8601 timestamp: {e}")
    if parsed.tzinfo is None:
        raise click.UsageError(
            "--now must include a timezone offset (e.g. 2026-08-28T12:00:00+00:00)"
        )
    return parsed


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
    # Gate finding L-6: this used to call load_stages bare and let a bad
    # config crash with a raw traceback from whatever CWD the user happened
    # to be in, unlike validate-config's try/except around the same call.
    try:
        stages_cfg = load_stages(stages_path)
    except (LoadError, ValidationError) as e:
        raise click.UsageError(f"{stages_path}: {e}")
    conn = _open_or_exit(mode, data_dir)
    try:
        apps = list_applications(conn)
    finally:
        conn.close()
    counts = funnel_counts(apps, stages_cfg)
    try:
        alerts = detect_stale(apps, stages_cfg, now=now)
    except ValueError as e:
        # Gate finding M-11: detect_stale raises the same ValueError whether
        # the cause is corrupt data (a genuinely unexpected bug -- let it
        # crash) or a --now the caller passed that lands before some app's
        # last-interaction timestamp. Only we know here which one it was:
        # --now exists so a reader can reproduce a pasted sample, and
        # pointing it at the wrong instant is the first mistake anyone makes
        # with it, so treat that case as a usage error rather than a crash.
        if now_str is not None:
            raise click.UsageError(str(e))
        raise
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

    Empty for a mode that has made no LLM calls yet (gate finding L-15: this
    used to say "empty until Phase B's first `@instrumented` call lands" --
    that call landed in B2, so it's been possible for this to be non-empty
    since then). The ledger exists ahead of Phase B's first real call on
    purpose (D5), so nothing has ever been uninstrumented.
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
    """Run the JD-extraction eval suite (33 cases) against the current `extract_jd`.

    Exits non-zero when the pass *rate* falls below `--min-pass-rate`, which
    defaults to `PASS_THRESHOLD`. Not a CI gate yet (gate finding L-15: this
    used to say wiring it in "would gate on 0/15" -- there are 33 cases, and
    the real reason is different now): without live traffic there's nothing
    for CI to run against beyond the fixed `--replay` recording, so gating
    today would gate on a frozen fixture's response to the prompt that
    produced it, not on the prompt's judgment against new input.

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
        # Gate finding M-12: persist each capture to disk the moment it
        # happens, not only after the whole run returns -- a run that raises
        # partway through (a safety refusal, a transient API error, Ctrl-C)
        # used to discard every capture already paid for. save_recording
        # merges rather than overwrites, so writing one entry at a time
        # accumulates correctly instead of each write erasing the last.
        client = RecordingClient(
            default_client(), on_captured=lambda key, text: save_recording({key: text})
        )

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
        # Every entry was already merged to disk by on_captured as the run
        # went; this is a final, redundant (and harmless -- save_recording
        # merges) flush plus the count for the message below.
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

# Gate finding M-6: `Application.fetch_status` defaulted to `ok` for every
# creation path, including paste and DLQ resolution -- so the DB claimed
# "fetched cleanly" about records that were never fetched. This is the one
# `FailureMode` case with no corresponding `dlq_*` status: it is defined but
# never actually produced by `fetcher.py` today (see FailureMode.other), so
# there is nothing live to map it to. `.get(..., FetchStatus.manual)` covers
# it defensively without inventing a status for a code path that doesn't run.
_DLQ_RESOLVED_FETCH_STATUS: dict[FailureMode, FetchStatus] = {
    FailureMode.paywall: FetchStatus.dlq_paywall,
    FailureMode.blocked: FetchStatus.dlq_blocked,
    FailureMode.timeout: FetchStatus.dlq_timeout,
    FailureMode.extraction_failed: FetchStatus.dlq_extraction_failed,
}


def _find_duplicate_application(
    conn, *, source_url: str | None, raw_text: str
) -> Application | None:
    """Gate finding M-8: `ingest` had no duplicate detection of any kind --
    the same JD file, or the same URL, ingested three times produced three
    Applications, feeding straight into `funnel_counts`/`detect_stale` just
    like M-1's shape did one command over. A URL is matched by exact
    equality; pasted text has no URL to key on, so it's matched by exact
    `source_raw` equality among the other paste-sourced applications.
    Decided: refuse re-ingesting silently. `ingest` notifies the caller and
    either confirms interactively or requires `--update`, then reprocesses
    into the *existing* row rather than creating a second one."""
    apps = list_applications(conn)
    if source_url is not None:
        return next((a for a in apps if a.source_url == source_url), None)
    return next((a for a in apps if a.source_url is None and a.source_raw == raw_text), None)


def _extract_and_create_application(
    conn,
    *,
    raw_text: str,
    source_url: str | None,
    company_override: str | None,
    fallback_company: str,
    fallback_title: str | None,
    fetch_status: FetchStatus = FetchStatus.ok,
    update_id: str | None = None,
) -> tuple[str, Application]:
    """Shared extract-then-store path for both `ingest` (URL and --paste) and
    `resolve-dlq` -- the DoD for Slice B4 requires the paste path produce the
    same Application shape as the URL path, so both funnel through here.

    Company precedence, highest first: `company_override` (the user typed
    `--company` explicitly -- that's a deliberate correction and wins over
    anything inferred), then `ExtractedJD.company` (extraction found a name
    in the JD text itself), then `fallback_company` (a URL-domain guess or
    "(pasted)", used only when extraction comes back null).

    `fetch_status` defaults to `ok` for the URL path, which really did fetch
    cleanly; callers on the paste and DLQ-resolution paths pass the status
    that actually describes how the raw text arrived.

    `update_id`, when given (gate finding M-8's confirmed-reprocess path),
    overwrites that existing Application's fields instead of creating a new
    one -- `stage` is deliberately left untouched, since a reprocess is a
    correction to the extracted record, not a reset of pipeline progress."""
    extracted = extract_jd(raw_text, conn=conn)
    fields = dict(
        source_url=source_url,
        source_raw=raw_text,
        fetch_status=fetch_status,
        title=extracted.title or fallback_title or "(untitled)",
        company=company_override or extracted.company or fallback_company,
        # The whole extraction, not just the field the title comes from: D9
        # splits extract from score because the intermediate output has
        # independent product value, and caching it needs it stored.
        extracted_jd=extracted.model_dump(),
    )
    if update_id is not None:
        update_application(conn, update_id, **fields)
        return update_id, get_application(conn, update_id)
    app = Application(**fields, stage=FIRST_STAGE)
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
    "--update",
    is_flag=True,
    default=False,
    help=(
        "If an application already exists for this URL/text, reprocess and "
        "overwrite it instead of asking. Required (rather than prompted) "
        "when reading pasted text from stdin, since stdin can't also answer "
        "the confirmation."
    ),
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
    update: bool,
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

    Re-ingesting a URL or exact pasted text already on file (gate finding
    M-8) does not silently create a second Application: it notifies you and
    asks before reprocessing into the existing one, or skips with `--update`.
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
            fallback_company_val = _company_from_url(url)
            fallback_title = result.title
            fetch_status_val = FetchStatus.ok
        else:
            raw_text = paste_file.read_text(encoding="utf-8") if paste_file else sys.stdin.read()
            if not raw_text.strip():
                click.echo("no JD text provided (empty stdin/file)", err=True)
                sys.exit(EXIT_USAGE)
            source_url = None
            fallback_company_val = "(pasted)"
            fallback_title = None
            fetch_status_val = FetchStatus.manual

        # Gate finding M-8: re-ingesting the same URL, or the same pasted
        # text, used to silently create a second Application every time.
        # Decided: notify and confirm before reprocessing into the existing
        # row. Reading pasted text from stdin (no --file) already consumed
        # stdin for the JD itself, so there is nothing left to confirm with
        # -- --update is required there instead of prompted.
        existing = _find_duplicate_application(conn, source_url=source_url, raw_text=raw_text)
        if existing is not None and not update:
            click.echo(
                f"an application already exists for this "
                f"{'URL' if source_url else 'pasted text'}: "
                f"{existing.id} ({existing.title!r}, created {existing.created_at.isoformat()})"
            )
            read_from_stdin = not url and not paste_file
            if read_from_stdin:
                click.echo("re-run with --update to reprocess and overwrite it", err=True)
                sys.exit(EXIT_USAGE)
            if not click.confirm("Re-process and overwrite it instead of skipping?", default=False):
                click.echo("no changes made")
                return

        try:
            app_id, app = _extract_and_create_application(
                conn,
                raw_text=raw_text,
                source_url=source_url,
                company_override=company,
                fallback_company=fallback_company_val,
                fallback_title=fallback_title,
                fetch_status=fetch_status_val,
                update_id=existing.id if existing is not None else None,
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
        except anthropic.APIError as e:
            # Gate finding H-6: a transient API error (rate limit, overload,
            # timeout, connection reset) propagated straight out of
            # `_raw_extraction_call` uncaught -- crashing `ingest` with a raw
            # traceback and, on `--paste`, losing the pasted text for good,
            # since nothing durable exists yet at the point of failure. D6's
            # contract is "produces an Application or a DLQEntry, never
            # crashes"; this did neither, for the single most likely failure
            # a live key introduces. Routed to `FailureMode.other`, which
            # M-6's own comment notes is defined and never produced by
            # `fetcher.py` -- there was already a slot waiting for exactly
            # this. Retry is `resolve-dlq`, same as any other DLQ entry.
            entry = DLQEntry(
                source_url=source_url or PASTED_SOURCE,
                failure_mode=FailureMode.other,
                error_detail=str(e),
            )
            entry_id = create_dlq_entry(conn, entry)
            click.echo(f"LLM API error; added to DLQ ({entry_id})")
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
        verb = "updated" if existing is not None else "created"
        click.echo(f"{verb} application {app_id}: {app.title}")
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
    "--company",
    default=None,
    help=(
        "Company name override, same precedence as ingest --paste's (gate "
        "finding L-12: this path funnels through the same helper but had no "
        "way to reach the correction it documents as highest-precedence)."
    ),
)
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_DATA_DIR,
    show_default=True,
    help="Directory holding mode DBs.",
)
def resolve_dlq(entry_id: str, paste_text: str, company: str | None, data_dir: Path) -> None:
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

        # Gate finding M-1: this used to skip the entry's current resolution
        # entirely, so re-running the same command against an already-resolved
        # entry created a second Application each time and re-stamped
        # `resolved_at` -- three runs, three duplicate Applications, feeding
        # straight into `funnel_counts`/`detect_stale`. Guarding on
        # `entry.resolution` also means a `wont_fix` entry can no longer be
        # converted to `manual_paste` from here; there is no "reopen" path,
        # which is a real, undecided gap rather than an oversight in this fix.
        if entry.resolution is not Resolution.unresolved:
            click.echo(
                f"DLQ entry {entry_id} is already resolved ({entry.resolution.value}); "
                "not creating another application"
            )
            # Gate finding M-9: exits 0, deliberately -- see EXIT_OK's
            # comment above. The entry *is* resolved, which is the state
            # this command exists to bring about; that this particular
            # invocation didn't do the resolving is what the message above
            # says, not what the exit code says.
            return

        fallback_company_val = (
            "(pasted)"
            if entry.source_url == PASTED_SOURCE
            else _company_from_url(entry.source_url)
        )
        try:
            app_id, _app = _extract_and_create_application(
                conn,
                raw_text=paste_text,
                source_url=None if entry.source_url == PASTED_SOURCE else entry.source_url,
                company_override=company,
                fallback_company=fallback_company_val,
                fallback_title=None,
                fetch_status=_DLQ_RESOLVED_FETCH_STATUS.get(
                    entry.failure_mode, FetchStatus.manual
                ),
            )
        except ExtractionParseError as e:
            # No new DLQ entry here -- one already exists and stays unresolved,
            # which is the correct record. Creating a second would duplicate the
            # queue on every retry.
            click.echo(f"extraction failed; DLQ entry {entry_id} left unresolved", err=True)
            click.echo(f"  {e}", err=True)
            sys.exit(EXIT_QUEUED)
        except anthropic.APIError as e:
            # Gate finding H-6, same class as ingest's -- see the comment
            # there. No new DLQ entry: the one being resolved stays
            # unresolved, which is already the correct record.
            click.echo(f"LLM API error; DLQ entry {entry_id} left unresolved", err=True)
            click.echo(f"  {e}", err=True)
            sys.exit(EXIT_QUEUED)
        except UnknownModelPricingError as e:
            # Gate finding L-11: `ingest` already turns this into a clean
            # exit-2 configuration message; this path funnels through the
            # same helper and used to let it out as a raw traceback instead.
            click.echo(f"configuration error: {e}", err=True)
            sys.exit(EXIT_USAGE)
        resolve_dlq_entry(conn, entry_id, Resolution.manual_paste, application_id=app_id)
        click.echo(f"created application {app_id} from DLQ entry {entry_id}")
    finally:
        conn.close()


def main() -> None:
    cli()
