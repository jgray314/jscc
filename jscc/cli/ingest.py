"""Record-producing commands: ingest, dlq list, resolve-dlq."""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

import anthropic
import click

from ..config import (
    load_pipeline,
)
from ..extraction import ExtractionParseError, extract_jd
from ..fetcher import fetch_jd
from ..llm_client import (
    UnknownModelPricingError,
)
from ..mode import DEFAULT_DATA_DIR
from ..models import (
    Application,
    DLQEntry,
    FailureMode,
    FetchStatus,
    Resolution,
)
from ..storage import (
    create_application,
    create_dlq_entry,
    get_application,
    list_applications,
    list_dlq_entries,
    resolve_dlq_entry,
    update_application,
)
from ._app import cli
from ._common import (
    DEFAULT_CONFIG_DIR,
    EXIT_QUEUED,
    EXIT_USAGE,
    FIRST_STAGE,
    _open_or_exit,
    _resolve_mode_or_exit,
    echo,
)


def _company_from_url(url: str) -> str:
    """Fallback company name for when extraction doesn't find one -- an
    ATS page whose JD text never names the employer, or the stub client.
    `_extract_and_create_application` prefers `ExtractedJD.company` over
    this whenever extraction returns one."""
    netloc = urlparse(url).netloc
    return netloc.removeprefix("www.") or url


# A DLQ entry needs a source_url (NOT NULL), and a pasted JD has none. The
# sentinel keeps the paste path's failures visible in `dlq list` rather than
# silently unrecoverable; `resolve-dlq` recognises it and skips URL-derived
# company inference. Same "(pasted)" spelling the company default already uses.
PASTED_SOURCE = "(pasted)"

# `Application.fetch_status` defaulted to `ok` for every
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
    """`ingest` had no duplicate detection of any kind --
    the same JD file, or the same URL, ingested three times produced three
    Applications, feeding straight into `funnel_counts`/`detect_stale` just
    like duplicate DLQ resolutions did one command over. A URL is matched by exact
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

    `update_id`, when given (the confirmed-reprocess path),
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

    Re-ingesting a URL or exact pasted text already on file does not silently create a second Application: it notifies you and
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
                echo(f"fetch failed ({result.failure_mode.value}); added to DLQ ({entry_id})")
                sys.exit(EXIT_QUEUED)
            raw_text = result.raw_text
            source_url: str | None = url
            fallback_company_val = _company_from_url(url)
            fallback_title = result.title
            fetch_status_val = FetchStatus.ok
        else:
            raw_text = paste_file.read_text(encoding="utf-8") if paste_file else sys.stdin.read()
            if not raw_text.strip():
                echo("no JD text provided (empty stdin/file)", err=True)
                sys.exit(EXIT_USAGE)
            source_url = None
            fallback_company_val = "(pasted)"
            fallback_title = None
            fetch_status_val = FetchStatus.manual

        # Re-ingesting the same URL, or the same pasted
        # text, used to silently create a second Application every time.
        # Decided: notify and confirm before reprocessing into the existing
        # row. Reading pasted text from stdin (no --file) already consumed
        # stdin for the JD itself, so there is nothing left to confirm with
        # -- --update is required there instead of prompted.
        existing = _find_duplicate_application(conn, source_url=source_url, raw_text=raw_text)
        if existing is not None and not update:
            echo(
                f"an application already exists for this "
                f"{'URL' if source_url else 'pasted text'}: "
                f"{existing.id} ({existing.title!r}, created {existing.created_at.isoformat()})"
            )
            read_from_stdin = not url and not paste_file
            if read_from_stdin:
                echo("re-run with --update to reprocess and overwrite it", err=True)
                sys.exit(EXIT_USAGE)
            if not click.confirm("Re-process and overwrite it instead of skipping?", default=False):
                echo("no changes made")
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
            echo(f"extraction failed; added to DLQ ({entry_id})")
            echo(f"  {e}", err=True)
            sys.exit(EXIT_QUEUED)
        except anthropic.APIError as e:
            # A transient API error (rate limit, overload,
            # timeout, connection reset) propagated straight out of
            # `_raw_extraction_call` uncaught -- crashing `ingest` with a raw
            # traceback and, on `--paste`, losing the pasted text for good,
            # since nothing durable exists yet at the point of failure. D6's
            # contract is "produces an Application or a DLQEntry, never
            # crashes"; this did neither, for the single most likely failure
            # a live key introduces. Routed to `FailureMode.other`, which
            # the fetch-status comment above notes is defined and never produced by
            # `fetcher.py` -- there was already a slot waiting for exactly
            # this. Retry is `resolve-dlq`, same as any other DLQ entry.
            entry = DLQEntry(
                source_url=source_url or PASTED_SOURCE,
                failure_mode=FailureMode.other,
                error_detail=str(e),
            )
            entry_id = create_dlq_entry(conn, entry)
            echo(f"LLM API error; added to DLQ ({entry_id})")
            echo(f"  {e}", err=True)
            sys.exit(EXIT_QUEUED)
        except UnknownModelPricingError as e:
            # Deliberately *not* DLQ'd, unlike the reviewer's suggested shape.
            # An unpriced model is a misconfiguration, not a bad JD: every
            # ingest would fail identically, so filling the queue with entries
            # that re-fail on resolve would bury the one thing worth reading.
            # Nothing was billed either -- the check runs before the request.
            echo(f"configuration error: {e}", err=True)
            sys.exit(EXIT_USAGE)
        verb = "updated" if existing is not None else "created"
        echo(f"{verb} application {app_id}: {app.title}")
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
@click.option("--all", "show_all", is_flag=True, help="Include already-resolved entries.")
def dlq_list(data_dir: Path, show_all: bool) -> None:
    """List DLQ entries for the active mode (unresolved-only by default)."""
    mode = _resolve_mode_or_exit()
    conn = _open_or_exit(mode, data_dir)
    try:
        entries = list_dlq_entries(conn, unresolved_only=not show_all)
    finally:
        conn.close()

    echo(f"[mode: {mode.value}]")
    if not entries:
        echo("no unresolved dlq entries")
        return

    echo(f"{'id':<38}{'failure_mode':<20}{'source_url'}")
    for entry in entries:
        echo(f"{entry.id:<38}{entry.failure_mode.value:<20}{entry.source_url}")


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
    help=("Company name override, with the same precedence as ingest --paste's."),
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
            echo(f"no DLQ entry with id {entry_id}", err=True)
            sys.exit(EXIT_USAGE)

        # This used to skip the entry's current resolution
        # entirely, so re-running the same command against an already-resolved
        # entry created a second Application each time and re-stamped
        # `resolved_at` -- three runs, three duplicate Applications, feeding
        # straight into `funnel_counts`/`detect_stale`. Guarding on
        # `entry.resolution` also means a `wont_fix` entry can no longer be
        # converted to `manual_paste` from here; there is no "reopen" path,
        # which is a real, undecided gap rather than an oversight in this fix.
        if entry.resolution is not Resolution.unresolved:
            echo(
                f"DLQ entry {entry_id} is already resolved ({entry.resolution.value}); "
                "not creating another application"
            )
            # Exits 0, deliberately -- see EXIT_OK's
            # comment above. The entry *is* resolved, which is the state
            # this command exists to bring about; that this particular
            # invocation didn't do the resolving is what the message above
            # says, not what the exit code says.
            return

        fallback_company_val = (
            "(pasted)" if entry.source_url == PASTED_SOURCE else _company_from_url(entry.source_url)
        )
        try:
            app_id, _app = _extract_and_create_application(
                conn,
                raw_text=paste_text,
                source_url=None if entry.source_url == PASTED_SOURCE else entry.source_url,
                company_override=company,
                fallback_company=fallback_company_val,
                fallback_title=None,
                fetch_status=_DLQ_RESOLVED_FETCH_STATUS.get(entry.failure_mode, FetchStatus.manual),
            )
        except ExtractionParseError as e:
            # No new DLQ entry here -- one already exists and stays unresolved,
            # which is the correct record. Creating a second would duplicate the
            # queue on every retry.
            echo(f"extraction failed; DLQ entry {entry_id} left unresolved", err=True)
            echo(f"  {e}", err=True)
            sys.exit(EXIT_QUEUED)
        except anthropic.APIError as e:
            # Same class of failure as ingest's -- see the comment
            # there. No new DLQ entry: the one being resolved stays
            # unresolved, which is already the correct record.
            echo(f"LLM API error; DLQ entry {entry_id} left unresolved", err=True)
            echo(f"  {e}", err=True)
            sys.exit(EXIT_QUEUED)
        except UnknownModelPricingError as e:
            # `ingest` already turns this into a clean
            # exit-2 configuration message; this path funnels through the
            # same helper and used to let it out as a raw traceback instead.
            echo(f"configuration error: {e}", err=True)
            sys.exit(EXIT_USAGE)
        resolve_dlq_entry(conn, entry_id, Resolution.manual_paste, application_id=app_id)
        echo(f"created application {app_id} from DLQ entry {entry_id}")
    finally:
        conn.close()
