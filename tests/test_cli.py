"""CLI-level tests via click.testing.CliRunner.

Adversarial finding M3: Phase A shipped `validate-config`, `db init`,
`seed --synthetic`, and `report` without any CliRunner coverage. Regressions
in mode routing or the `seed --synthetic` refusal on JSCC_DATA=real would
land silently. This module closes that gap.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from jscc.cli import cli
from jscc.mode import ENV_VAR


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "config"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---- validate-config ----------------------------------------------------------

def test_validate_config_success(runner: CliRunner) -> None:
    result = runner.invoke(cli, ["validate-config", "--config-dir", str(CONFIG_DIR)])
    assert result.exit_code == 0, result.output
    assert "all configs valid" in result.output


def test_validate_config_missing_dir(runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(
        cli, ["validate-config", "--config-dir", str(tmp_path / "nope")]
    )
    assert result.exit_code == 1
    assert "FAIL" in result.output


# ---- db init ------------------------------------------------------------------

def test_db_init_default_synthetic(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    result = runner.invoke(cli, ["db", "init", "--data-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "synthetic.db" in result.output
    assert "mode marker: synthetic" in result.output
    assert (tmp_path / "synthetic.db").exists()


def test_db_init_respects_env_real(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_VAR, "real")
    result = runner.invoke(cli, ["db", "init", "--data-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "real.db").exists()
    assert "mode marker: real" in result.output


def test_db_init_bogus_env_exits_nonzero(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_VAR, "production")
    result = runner.invoke(cli, ["db", "init", "--data-dir", str(tmp_path)])
    assert result.exit_code != 0


# ---- seed ---------------------------------------------------------------------

def test_seed_success_default_synthetic(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    result = runner.invoke(
        cli,
        [
            "seed",
            "--data-dir",
            str(tmp_path),
            "--now",
            "2026-08-28T12:00:00+00:00",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "seeded" in result.output
    assert (tmp_path / "synthetic.db").exists()


def test_seed_refuses_in_real_mode(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M3 core coverage: --synthetic must refuse when JSCC_DATA=real."""
    monkeypatch.setenv(ENV_VAR, "real")
    result = runner.invoke(cli, ["seed", "--data-dir", str(tmp_path)])
    assert result.exit_code == 2
    assert "refusing to seed" in result.output
    assert not (tmp_path / "real.db").exists()
    assert not (tmp_path / "synthetic.db").exists()


def test_seed_with_pinned_now_reproducible(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """H3 regression: same seed + same --now → same DB bytes."""
    import sqlite3

    monkeypatch.delenv(ENV_VAR, raising=False)
    args_common = [
        "--random-seed", "42",
        "--now", "2026-08-28T12:00:00+00:00",
    ]

    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"

    runner.invoke(cli, ["seed", "--data-dir", str(dir_a), *args_common])
    runner.invoke(cli, ["seed", "--data-dir", str(dir_b), *args_common])

    # A9 broadening: the A7 version of this test only queried applications, and
    # every application row DID get a seeded id — but every non-first Interaction
    # was constructed without an explicit id, silently falling back to
    # default_factory=uuid4 → os.urandom. Hashing every table catches that miss
    # and any future one like it.
    def dump_tables(path: Path) -> dict[str, list[tuple]]:
        conn = sqlite3.connect(str(path))
        try:
            out: dict[str, list[tuple]] = {}
            for table in ("applications", "contacts", "interactions", "dlq_entries"):
                cols = [
                    row[1]
                    for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
                ]
                col_list = ", ".join(cols)
                out[table] = conn.execute(
                    f"SELECT {col_list} FROM {table} ORDER BY id"
                ).fetchall()
            return out
        finally:
            conn.close()

    tables_a = dump_tables(dir_a / "synthetic.db")
    tables_b = dump_tables(dir_b / "synthetic.db")
    for table in ("applications", "contacts", "interactions", "dlq_entries"):
        assert tables_a[table] == tables_b[table], f"drift in {table}"
        assert len(tables_a[table]) > 0, f"empty {table}"


def test_seed_now_missing_timezone_fails(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    result = runner.invoke(
        cli,
        ["seed", "--data-dir", str(tmp_path), "--now", "2026-08-28T12:00:00"],
    )
    assert result.exit_code != 0
    assert "timezone" in result.output


def test_seed_now_bogus_string_fails(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    result = runner.invoke(
        cli, ["seed", "--data-dir", str(tmp_path), "--now", "not-a-date"]
    )
    assert result.exit_code != 0
    assert "ISO-8601" in result.output


# ---- report -------------------------------------------------------------------

def test_report_on_seeded_db(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    runner.invoke(
        cli,
        [
            "seed",
            "--data-dir", str(tmp_path),
            "--now", "2026-08-28T12:00:00+00:00",
        ],
    )
    result = runner.invoke(
        cli,
        ["report", "--data-dir", str(tmp_path), "--config-dir", str(CONFIG_DIR)],
    )
    assert result.exit_code == 0, result.output
    # Funnel section and staleness section both render
    assert "applied" in result.output.lower()


def test_report_empty_db(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    runner.invoke(cli, ["db", "init", "--data-dir", str(tmp_path)])
    result = runner.invoke(
        cli,
        ["report", "--data-dir", str(tmp_path), "--config-dir", str(CONFIG_DIR)],
    )
    assert result.exit_code == 0, result.output
