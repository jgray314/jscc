"""Record-producing commands: ingest, dlq list, resolve-dlq.

`resolve-dlq`'s actual resolution logic lives in `jscc/dlq.py` (Slice E2b) --
this module is the click translation of that shared result, not the logic
itself, so the dashboard's resolve form can call the same function."""

from __future__ import annotations

import sys
from pathlib import Path

import anthropic
import click

from ..config import (
    load_pipeline,
)
from ..dlq import DLQResolveOutcome, resolve_dlq_entry_via_paste
from ..extraction import ExtractionParseError
from ..fetcher import fetch_jd
from ..ingest_logic import (
    PASTED_SOURCE,
    STUB_IN_REAL_MODE_MESSAGE,
    company_from_url,
    extract_and_create_application,
    find_duplicate_application,
    stub_extractor_in_real_mode,
)
from ..llm_client import (
    UnknownModelPricingError,
)
from ..mode import DEFAULT_DATA_DIR, Mode
from ..models import (
    DLQEntry,
    FailureMode,
    FetchStatus,
)
from ..storage import (
    create_dlq_entry,
    list_dlq_entries,
)
from ._app import cli
from ._common import (
    DEFAULT_CONFIG_DIR,
    EXIT_QUEUED,
    EXIT_USAGE,
    _open_or_exit,
    _resolve_mode_or_exit,
    echo,
)


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
    if stub_extractor_in_real_mode(mode):
        echo(STUB_IN_REAL_MODE_MESSAGE, err=True)
        sys.exit(EXIT_USAGE)
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
            fallback_company_val = company_from_url(url)
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
        existing = find_duplicate_application(conn, source_url=source_url, raw_text=raw_text)
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
            app_id, app = extract_and_create_application(
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
            # A transient API error (rate limit, overload, timeout, connection
            # reset) used to propagate out uncaught and crash `ingest` with a
            # traceback. D6's contract is "produces an Application or a DLQEntry,
            # never crashes", so it is routed to `FailureMode.other` (the slot
            # for a failure that is not a fetch failure). Retry is `resolve-dlq`,
            # same as any other DLQ entry. Known limit: the entry stores the
            # source URL, not the text, so on `--paste` the pasted text is still
            # lost and `resolve-dlq` needs it pasted again (threat model T10).
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
    entry resolved. Same resolution function (`jscc/dlq.py`) the dashboard's
    resolve form (Slice E2b) calls -- this command only translates its typed
    result into click's exit-code contract.
    """
    mode = _resolve_mode_or_exit()
    conn = _open_or_exit(mode, data_dir)
    try:
        result = resolve_dlq_entry_via_paste(
            conn, entry_id, paste_text, company=company, refuse_stub=mode is Mode.real
        )
    finally:
        conn.close()

    if result.outcome is DLQResolveOutcome.not_found:
        echo(f"no DLQ entry with id {entry_id}", err=True)
        sys.exit(EXIT_USAGE)
    if result.outcome is DLQResolveOutcome.already_resolved:
        # This used to skip the entry's current resolution
        # entirely, so re-running the same command against an already-resolved
        # entry created a second Application each time and re-stamped
        # `resolved_at` -- three runs, three duplicate Applications, feeding
        # straight into `funnel_counts`/`detect_stale`. Guarding on
        # `entry.resolution` also means a `wont_fix` entry can no longer be
        # converted to `manual_paste` from here; there is no "reopen" path,
        # which is a real, undecided gap rather than an oversight in this fix.
        echo(
            f"DLQ entry {entry_id} is already resolved "
            f"({result.prior_resolution.value}); not creating another application"
        )
        # Exits 0, deliberately -- see EXIT_OK's
        # comment above. The entry *is* resolved, which is the state
        # this command exists to bring about; that this particular
        # invocation didn't do the resolving is what the message above
        # says, not what the exit code says.
        return
    if result.outcome is DLQResolveOutcome.duplicate:
        echo(
            f"an application already exists for this posting ({result.application_id}); "
            f"DLQ entry {entry_id} marked resolved (wont_fix) and linked to it, "
            "nothing created"
        )
        return
    if result.outcome is DLQResolveOutcome.extraction_failed:
        # No new DLQ entry here -- one already exists and stays unresolved,
        # which is the correct record. Creating a second would duplicate the
        # queue on every retry.
        echo(f"extraction failed; DLQ entry {entry_id} left unresolved", err=True)
        echo(f"  {result.detail}", err=True)
        sys.exit(EXIT_QUEUED)
    if result.outcome is DLQResolveOutcome.llm_api_error:
        # Same class of failure as ingest's -- see the comment there. No new
        # DLQ entry: the one being resolved stays unresolved, which is
        # already the correct record.
        echo(f"LLM API error; DLQ entry {entry_id} left unresolved", err=True)
        echo(f"  {result.detail}", err=True)
        sys.exit(EXIT_QUEUED)
    if result.outcome is DLQResolveOutcome.config_error:
        # `ingest` already turns this into a clean exit-2 configuration
        # message; this path funnels through the same helper and used to
        # let it out as a raw traceback instead.
        echo(f"configuration error: {result.detail}", err=True)
        sys.exit(EXIT_USAGE)
    echo(f"created application {result.application_id} from DLQ entry {entry_id}")
