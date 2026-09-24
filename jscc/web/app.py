"""FastAPI app factory for the JSCC dashboard (Slices E1-E2b).

Read path through E2a: the index route opens the active mode's DB (same
`JSCC_DATA` / `--data-dir` contract the CLI uses) and renders the pipeline
table, funnel counts, and stale-alert list -- the same pure functions
`jscc report` renders as text (`jscc/report.py`), so the two surfaces cannot
drift on what counts as stale. E2b adds the one write path in this phase:
application detail, a DLQ list, and a DLQ resolve form that calls
`jscc/dlq.py`'s `resolve_dlq_entry_via_paste` -- the exact function
`resolve-dlq` calls, so the CLI and the dashboard cannot drift on DLQ
resolution either. `create_app` takes `data_dir` / `config_dir` as explicit
arguments rather than reading globals at import time -- the CLI's `serve`
command and the test suite both need to point the same app at different
directories.

Request guards (Phase E gate L1-1): every request's `Host` must be on an
allowlist (loopback names, plus the address `jscc serve` was told to bind), and
a state-changing request must not come from another origin. Binding to
127.0.0.1 keeps other machines out, but it does not stop a web page in the
user's own browser from reaching the server: DNS rebinding points an attacker's
domain at 127.0.0.1, and the browser then treats the dashboard as that page's
own site. The Host check refuses that (the request names the attacker's
domain); the Origin check refuses a cross-site form post.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from ..config import LoadError, StagesConfig, load_stages
from ..dlq import DLQResolveOutcome, resolve_dlq_entry_via_paste
from ..mode import DEFAULT_DATA_DIR, InvalidModeError, Mode, resolve_mode
from ..paths import PACKAGE_ROOT
from ..report import detect_stale, funnel_counts, group_by_stage
from ..storage import (
    ModeMismatchError,
    get_application,
    list_applications,
    list_contacts,
    list_dlq_entries,
    list_interactions,
    open_for_mode,
)

DEFAULT_CONFIG_DIR = PACKAGE_ROOT / "config"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Names a browser uses to reach a server bound to the loopback interface.
LOOPBACK_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _is_http_url(value: object) -> bool:
    """Whether `value` is an http(s) URL. Stored URLs are untrusted text (a DLQ
    entry keeps whatever `--url` was given, even a scheme the fetcher refused),
    so a template links only ones a browser will treat as a web page."""
    if not isinstance(value, str):
        return False
    return urlsplit(value.strip()).scheme.lower() in ("http", "https")


templates.env.tests["http_url"] = _is_http_url


def _hostname(host_header: str) -> str:
    """The hostname in a `Host` header: port stripped, brackets removed from an
    IPv6 literal, lowercased."""
    host = host_header.strip().lower()
    if host.startswith("["):
        return host[1:].partition("]")[0]
    return host.partition(":")[0]


def _parse_now_query(now_str: str | None) -> datetime | None:
    """Parse an optional `?now=` override. Mirrors the CLI's `--now` (see
    `_common._parse_now`) so a pinned seed's staleness block is reproducible
    from the browser the same way `jscc report --now ...` reproduces it from
    the shell -- same format, same tz requirement, same error framing.
    """
    if now_str is None:
        return None
    try:
        parsed = datetime.fromisoformat(now_str)
    except ValueError as e:
        raise HTTPException(
            status_code=400, detail=f"now is not a valid ISO-8601 timestamp: {e}"
        ) from e
    if parsed.tzinfo is None:
        raise HTTPException(
            status_code=400,
            detail="now must include a timezone offset (e.g. 2026-08-28T12:00:00+00:00)",
        )
    return parsed


def create_app(
    *,
    data_dir: Path = DEFAULT_DATA_DIR,
    config_dir: Path = DEFAULT_CONFIG_DIR,
    allowed_hosts: Iterable[str] = LOOPBACK_HOSTS,
) -> FastAPI:
    app = FastAPI(title="JSCC Dashboard")
    app.state.data_dir = data_dir
    app.state.config_dir = config_dir
    allowed = frozenset(h.lower() for h in allowed_hosts)

    @app.middleware("http")
    async def guard_host_and_origin(request: Request, call_next):
        host_header = request.headers.get("host", "")
        if _hostname(host_header) not in allowed:
            return PlainTextResponse("Host not allowed", status_code=400)
        if request.method not in _SAFE_METHODS:
            origin = request.headers.get("origin")
            # A browser sends Origin on every cross-origin POST; "null" is what
            # a sandboxed or redirected context sends. A request with neither
            # header is not a browser form post (curl, the test client).
            if (
                origin is not None
                and urlsplit(origin).netloc.lower() != host_header.strip().lower()
            ):
                return PlainTextResponse("Cross-origin request refused", status_code=403)
            if request.headers.get("sec-fetch-site") not in (None, "same-origin", "none"):
                return PlainTextResponse("Cross-origin request refused", status_code=403)
        return await call_next(request)

    def get_mode() -> Mode:
        try:
            return resolve_mode()
        except InvalidModeError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    def get_conn(mode: Mode = Depends(get_mode)) -> sqlite3.Connection:
        try:
            # The framework may run this dependency's setup, the handler and the
            # teardown on different worker threads. The connection belongs to one
            # request and is never used by two threads at once, so the same-thread
            # check only turns a concurrent page load into a 500.
            conn = open_for_mode(mode, app.state.data_dir, check_same_thread=False)
        except ModeMismatchError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
        try:
            yield conn
        finally:
            conn.close()

    def get_stages_config() -> StagesConfig:
        stages_path = app.state.config_dir / "stages.yaml"
        try:
            return load_stages(stages_path)
        except (LoadError, ValidationError) as e:
            raise HTTPException(status_code=500, detail=f"{stages_path}: {e}") from e

    @app.get("/")
    def index(
        request: Request,
        now: str | None = Query(default=None),
        mode: Mode = Depends(get_mode),
        conn: sqlite3.Connection = Depends(get_conn),
        stages_cfg: StagesConfig = Depends(get_stages_config),
    ):
        pinned_now = _parse_now_query(now)
        apps = list_applications(conn)
        counts = funnel_counts(apps, stages_cfg)
        pipeline = group_by_stage(apps, stages_cfg)
        try:
            alerts = detect_stale(apps, stages_cfg, now=pinned_now)
        except ValueError as e:
            # Same split as `jscc report`: a bad --now the caller can fix is a
            # usage error (400); the wall clock producing this is a real bug
            # in stored data and should 500 like anything else unexpected.
            if now is not None:
                raise HTTPException(status_code=400, detail=str(e)) from e
            raise
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "mode": mode.value,
                "is_synthetic": mode is Mode.synthetic,
                "application_count": len(apps),
                "stages": stages_cfg.stages,
                "counts": counts,
                "total_count": sum(counts.values()),
                "pipeline": pipeline,
                "alerts": alerts,
            },
        )

    @app.get("/applications/{app_id}")
    def application_detail(
        request: Request,
        app_id: str,
        mode: Mode = Depends(get_mode),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        application = get_application(conn, app_id)
        if application is None:
            raise HTTPException(status_code=404, detail=f"no application with id {app_id}")
        return templates.TemplateResponse(
            request,
            "application_detail.html",
            {
                "mode": mode.value,
                "is_synthetic": mode is Mode.synthetic,
                "app": application,
                "contacts": list_contacts(conn, app_id),
                "interactions": list_interactions(conn, app_id),
            },
        )

    @app.get("/dlq")
    def dlq_list(
        request: Request,
        show_all: bool = Query(default=False, alias="all"),
        mode: Mode = Depends(get_mode),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        entries = list_dlq_entries(conn, unresolved_only=not show_all)
        return templates.TemplateResponse(
            request,
            "dlq_list.html",
            {
                "mode": mode.value,
                "is_synthetic": mode is Mode.synthetic,
                "entries": entries,
                "show_all": show_all,
            },
        )

    @app.get("/dlq/{entry_id}/resolve")
    def dlq_resolve_form(
        request: Request,
        entry_id: str,
        mode: Mode = Depends(get_mode),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        entries = list_dlq_entries(conn, unresolved_only=False)
        entry = next((e for e in entries if e.id == entry_id), None)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"no DLQ entry with id {entry_id}")
        return templates.TemplateResponse(
            request,
            "dlq_resolve.html",
            {
                "mode": mode.value,
                "is_synthetic": mode is Mode.synthetic,
                "entry": entry,
                "result": None,
                "outcome": DLQResolveOutcome,
            },
        )

    @app.post("/dlq/{entry_id}/resolve")
    def dlq_resolve_submit(
        request: Request,
        entry_id: str,
        paste_text: str = Form(...),
        company: str = Form(default=""),
        mode: Mode = Depends(get_mode),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        entries = list_dlq_entries(conn, unresolved_only=False)
        entry = next((e for e in entries if e.id == entry_id), None)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"no DLQ entry with id {entry_id}")
        if not paste_text.strip():
            raise HTTPException(status_code=400, detail="paste_text must not be empty")
        result = resolve_dlq_entry_via_paste(
            conn, entry_id, paste_text, company=company.strip() or None
        )
        return templates.TemplateResponse(
            request,
            "dlq_resolve.html",
            {
                "mode": mode.value,
                "is_synthetic": mode is Mode.synthetic,
                "entry": entry,
                "result": result,
                "outcome": DLQResolveOutcome,
                "submitted_paste_text": paste_text,
                "submitted_company": company,
            },
        )

    return app
