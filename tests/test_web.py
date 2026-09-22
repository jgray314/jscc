"""Slice E1 smoke test: the dashboard app boots and reads the active mode's DB.

Mirrors the CliRunner pattern in test_cli.py -- isolate JSCC_DATA, point at a
tmp_path data dir, seed a known fixture, and assert on the response instead of
inspecting internals. FastAPI's TestClient drives the app in-process (no
socket), so this stays as fast as any other unit test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jscc.mode import ENV_VAR, Mode
from jscc.seed import seed_synthetic
from jscc.storage import open_for_mode
from jscc.web import create_app


@pytest.fixture
def synthetic_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv(ENV_VAR, raising=False)
    conn = open_for_mode(Mode.synthetic, tmp_path)
    try:
        seed_synthetic(conn, random_seed=1)
    finally:
        conn.close()
    return tmp_path


def test_index_boots_and_shows_seeded_row_count(synthetic_data_dir: Path) -> None:
    app = create_app(data_dir=synthetic_data_dir)
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    conn = open_for_mode(Mode.synthetic, synthetic_data_dir)
    try:
        from jscc.storage import list_applications

        expected_count = len(list_applications(conn))
    finally:
        conn.close()
    assert expected_count > 0
    assert f"{expected_count} application" in response.text
    assert "Mode: <strong>synthetic</strong>" in response.text


def test_index_shows_synthetic_mode_banner(synthetic_data_dir: Path) -> None:
    app = create_app(data_dir=synthetic_data_dir)
    client = TestClient(app)

    response = client.get("/")

    assert "SYNTHETIC MODE" in response.text


def test_index_real_mode_hides_synthetic_banner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_VAR, "real")
    conn = open_for_mode(Mode.real, tmp_path)
    conn.close()

    app = create_app(data_dir=tmp_path)
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "SYNTHETIC MODE" not in response.text
    assert "Mode: <strong>real</strong>" in response.text
