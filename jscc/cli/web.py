"""`jscc serve` -- boots the dashboard (Slice E1, ADR-007)."""

from __future__ import annotations

from pathlib import Path

import click
import uvicorn

from ..mode import DEFAULT_DATA_DIR
from ..web import create_app
from ._app import cli
from ._common import DEFAULT_CONFIG_DIR, echo


@cli.command("serve")
@click.option(
    "--data-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_DATA_DIR,
    show_default=True,
    help="Directory holding mode DBs (data/<mode>.db).",
)
@click.option(
    "--config-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_CONFIG_DIR,
    show_default=True,
    help="Directory containing stages.yaml and profile.*.yaml.",
)
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8000, show_default=True, type=int)
def serve(data_dir: Path, config_dir: Path, host: str, port: int) -> None:
    """Run the JSCC dashboard for the active mode (JSCC_DATA, default synthetic).

    Local-only per D4 -- no BYOK, no public hosting in v1. Binds to
    127.0.0.1 by default; pass --host explicitly to expose it further.
    """
    app = create_app(data_dir=data_dir, config_dir=config_dir)
    echo(f"serving on http://{host}:{port} (data dir: {data_dir})")
    uvicorn.run(app, host=host, port=port)
