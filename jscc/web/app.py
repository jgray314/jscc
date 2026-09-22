"""FastAPI app factory for the JSCC dashboard (Slices E1-E2a).

Read path only through E2a: the index route opens the active mode's DB (same
`JSCC_DATA` / `--data-dir` contract the CLI uses) and renders the pipeline
table, funnel counts, and stale-alert list -- the same pure functions
`jscc report` renders as text (`jscc/report.py`), so the two surfaces cannot
drift on what counts as stale. `create_app` takes `data_dir` / `config_dir`
as explicit arguments rather than reading globals at import time -- the CLI's
`serve` command and the test suite both need to point the same app at
different directories.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from ..config import LoadError, StagesConfig, load_stages
from ..mode import DEFAULT_DATA_DIR, InvalidModeError, Mode, resolve_mode
from ..paths import PACKAGE_ROOT
from ..report import detect_stale, funnel_counts, group_by_stage
from ..storage import ModeMismatchError, list_applications, open_for_mode

DEFAULT_CONFIG_DIR = PACKAGE_ROOT / "config"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


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
) -> FastAPI:
    app = FastAPI(title="JSCC Dashboard")
    app.state.data_dir = data_dir
    app.state.config_dir = config_dir

    def get_mode() -> Mode:
        try:
            return resolve_mode()
        except InvalidModeError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e

    def get_conn(mode: Mode = Depends(get_mode)) -> sqlite3.Connection:
        try:
            conn = open_for_mode(mode, app.state.data_dir)
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

    return app
