"""FastAPI app factory for the JSCC dashboard (Slice E1).

Read path only at this slice: the index route opens the active mode's DB
(same `JSCC_DATA` / `--data-dir` contract the CLI uses) and shows the row
count, so E1's DoD ("boots, shows seeded DB row count") is checkable without
any of E2a/E2b's views existing yet. `create_app` takes `data_dir` /
`config_dir` as explicit arguments rather than reading globals at import time
-- the CLI's `serve` command and the test suite both need to point the same
app at different directories.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.templating import Jinja2Templates

from ..mode import DEFAULT_DATA_DIR, InvalidModeError, Mode, resolve_mode
from ..paths import PACKAGE_ROOT
from ..storage import ModeMismatchError, list_applications, open_for_mode

DEFAULT_CONFIG_DIR = PACKAGE_ROOT / "config"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


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

    @app.get("/")
    def index(
        request: Request,
        mode: Mode = Depends(get_mode),
        conn: sqlite3.Connection = Depends(get_conn),
    ):
        apps = list_applications(conn)
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "mode": mode.value,
                "is_synthetic": mode is Mode.synthetic,
                "application_count": len(apps),
            },
        )

    return app
