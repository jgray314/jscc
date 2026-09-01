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


# ---- costs ----------------------------------------------------------------------

def test_costs_empty_ledger(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D5: the ledger exists (and is CLI-visible) ahead of any real LLM call."""
    monkeypatch.delenv(ENV_VAR, raising=False)
    runner.invoke(cli, ["db", "init", "--data-dir", str(tmp_path)])
    result = runner.invoke(cli, ["costs", "--data-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "no LLM calls recorded yet" in result.output


def test_costs_summarizes_recorded_calls(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    runner.invoke(cli, ["db", "init", "--data-dir", str(tmp_path)])

    from jscc.instrumentation import LLMResult, instrumented
    from jscc.mode import Mode
    from jscc.storage import open_for_mode

    @instrumented("extraction")
    def fake_llm_call(conn, model, prompt):
        return LLMResult(output=None, input_tokens=1, output_tokens=1, cost_usd=0.5)

    conn = open_for_mode(Mode.synthetic, tmp_path)
    try:
        fake_llm_call(conn, "claude-haiku", "prompt")
    finally:
        conn.close()

    result = runner.invoke(cli, ["costs", "--data-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "extraction" in result.output
    assert "0.5000" in result.output


# ---- ingest / dlq ---------------------------------------------------------------

def test_ingest_url_success_creates_application(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    runner.invoke(cli, ["db", "init", "--data-dir", str(tmp_path)])

    from jscc.fetcher import FetchResult

    monkeypatch.setattr(
        "jscc.cli.fetch_jd",
        lambda url, **kw: FetchResult(ok=True, title="Senior Engineer", raw_text="a" * 300),
    )

    result = runner.invoke(
        cli, ["ingest", "--url", "https://example.com/jobs/1", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert "created application" in result.output.lower()

    from jscc.mode import Mode
    from jscc.storage import list_applications, open_for_mode

    conn = open_for_mode(Mode.synthetic, tmp_path)
    apps = list_applications(conn)
    conn.close()
    assert len(apps) == 1
    assert apps[0].source_url == "https://example.com/jobs/1"


def test_ingest_passes_playwright_flag_from_pipeline_config(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    runner.invoke(cli, ["db", "init", "--data-dir", str(tmp_path)])

    from jscc.fetcher import FetchResult

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "pipeline.yaml").write_text("playwright_fallback: true\n", encoding="utf-8")

    captured_kwargs = {}

    def fake_fetch_jd(url, **kw):
        captured_kwargs.update(kw)
        return FetchResult(ok=True, title="Senior Engineer", raw_text="a" * 300)

    monkeypatch.setattr("jscc.cli.fetch_jd", fake_fetch_jd)

    result = runner.invoke(
        cli,
        [
            "ingest",
            "--url",
            "https://example.com/jobs/1",
            "--data-dir",
            str(tmp_path),
            "--config-dir",
            str(config_dir),
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured_kwargs.get("use_playwright_fallback") is True


def test_ingest_url_failure_creates_dlq_entry_not_application(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    runner.invoke(cli, ["db", "init", "--data-dir", str(tmp_path)])

    from jscc.fetcher import FetchResult
    from jscc.models import FailureMode

    monkeypatch.setattr(
        "jscc.cli.fetch_jd",
        lambda url, **kw: FetchResult(
            ok=False, failure_mode=FailureMode.blocked, error_detail="HTTP 403"
        ),
    )

    result = runner.invoke(
        cli, ["ingest", "--url", "https://example.com/jobs/2", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert "dlq" in result.output.lower()

    from jscc.mode import Mode
    from jscc.storage import list_applications, list_dlq_entries, open_for_mode

    conn = open_for_mode(Mode.synthetic, tmp_path)
    apps = list_applications(conn)
    entries = list_dlq_entries(conn, unresolved_only=False)
    conn.close()
    assert apps == []
    assert len(entries) == 1
    assert entries[0].source_url == "https://example.com/jobs/2"
    assert entries[0].failure_mode.value == "blocked"


def test_ingest_never_crashes_on_fetch_exception_shaped_failure(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DoD: ingest --url produces Application OR DLQEntry, never crashes."""
    monkeypatch.delenv(ENV_VAR, raising=False)
    runner.invoke(cli, ["db", "init", "--data-dir", str(tmp_path)])

    from jscc.fetcher import FetchResult
    from jscc.models import FailureMode

    monkeypatch.setattr(
        "jscc.cli.fetch_jd",
        lambda url, **kw: FetchResult(
            ok=False, failure_mode=FailureMode.timeout, error_detail="timed out"
        ),
    )

    result = runner.invoke(
        cli, ["ingest", "--url", "https://example.com/jobs/3", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output


def test_ingest_paste_stdin_creates_application_same_shape_as_url(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DoD: paste path produces the same Application shape as the URL path."""
    monkeypatch.delenv(ENV_VAR, raising=False)
    runner.invoke(cli, ["db", "init", "--data-dir", str(tmp_path)])

    result = runner.invoke(
        cli,
        ["ingest", "--paste", "--data-dir", str(tmp_path)],
        input="Senior Engineer at Rift Cloud. " * 20,
    )
    assert result.exit_code == 0, result.output
    assert "created application" in result.output.lower()

    from jscc.mode import Mode
    from jscc.storage import list_applications, open_for_mode

    conn = open_for_mode(Mode.synthetic, tmp_path)
    apps = list_applications(conn)
    conn.close()
    assert len(apps) == 1
    assert apps[0].source_url is None
    assert apps[0].company == "(pasted)"
    assert apps[0].source_raw.startswith("Senior Engineer at Rift Cloud.")


def test_ingest_paste_file_reads_from_file_not_stdin(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    runner.invoke(cli, ["db", "init", "--data-dir", str(tmp_path)])

    jd_file = tmp_path / "jd.txt"
    jd_file.write_text("Staff Engineer at Timber Motors. " * 20, encoding="utf-8")

    result = runner.invoke(
        cli,
        [
            "ingest",
            "--file",
            str(jd_file),
            "--company",
            "Timber Motors",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output

    from jscc.mode import Mode
    from jscc.storage import list_applications, open_for_mode

    conn = open_for_mode(Mode.synthetic, tmp_path)
    apps = list_applications(conn)
    conn.close()
    assert len(apps) == 1
    assert apps[0].company == "Timber Motors"
    assert apps[0].source_raw.startswith("Staff Engineer at Timber Motors.")


def test_ingest_paste_empty_input_exits_nonzero_no_application(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    runner.invoke(cli, ["db", "init", "--data-dir", str(tmp_path)])

    result = runner.invoke(
        cli, ["ingest", "--paste", "--data-dir", str(tmp_path)], input="   \n"
    )
    assert result.exit_code != 0

    from jscc.mode import Mode
    from jscc.storage import list_applications, open_for_mode

    conn = open_for_mode(Mode.synthetic, tmp_path)
    apps = list_applications(conn)
    conn.close()
    assert apps == []


def test_ingest_url_and_paste_together_is_usage_error(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    runner.invoke(cli, ["db", "init", "--data-dir", str(tmp_path)])

    result = runner.invoke(
        cli,
        ["ingest", "--url", "https://example.com/jobs/1", "--paste", "--data-dir", str(tmp_path)],
        input="text",
    )
    assert result.exit_code != 0


def test_ingest_neither_url_nor_paste_is_usage_error(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    runner.invoke(cli, ["db", "init", "--data-dir", str(tmp_path)])

    result = runner.invoke(cli, ["ingest", "--data-dir", str(tmp_path)])
    assert result.exit_code != 0


def test_dlq_list_shows_unresolved_entries(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    runner.invoke(cli, ["db", "init", "--data-dir", str(tmp_path)])

    from jscc.models import DLQEntry, FailureMode
    from jscc.mode import Mode
    from jscc.storage import create_dlq_entry, open_for_mode

    conn = open_for_mode(Mode.synthetic, tmp_path)
    create_dlq_entry(
        conn,
        DLQEntry(
            source_url="https://example.com/jobs/4",
            failure_mode=FailureMode.paywall,
            error_detail="HTTP 402",
        ),
    )
    conn.close()

    result = runner.invoke(cli, ["dlq", "list", "--data-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "https://example.com/jobs/4" in result.output
    assert "paywall" in result.output


def test_dlq_list_empty(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    runner.invoke(cli, ["db", "init", "--data-dir", str(tmp_path)])
    result = runner.invoke(cli, ["dlq", "list", "--data-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "no unresolved dlq entries" in result.output.lower()


def test_resolve_dlq_paste_text_creates_application_and_resolves_entry(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    runner.invoke(cli, ["db", "init", "--data-dir", str(tmp_path)])

    from jscc.models import DLQEntry, FailureMode
    from jscc.mode import Mode
    from jscc.storage import create_dlq_entry, list_applications, list_dlq_entries, open_for_mode

    conn = open_for_mode(Mode.synthetic, tmp_path)
    entry_id = create_dlq_entry(
        conn,
        DLQEntry(
            source_url="https://example.com/jobs/5",
            failure_mode=FailureMode.blocked,
            error_detail="HTTP 403",
        ),
    )
    conn.close()

    result = runner.invoke(
        cli,
        [
            "resolve-dlq",
            entry_id,
            "--paste-text",
            "Senior Engineer at Rift Cloud. " * 20,
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output

    conn = open_for_mode(Mode.synthetic, tmp_path)
    apps = list_applications(conn)
    unresolved = list_dlq_entries(conn, unresolved_only=True)
    conn.close()
    assert len(apps) == 1
    assert apps[0].source_url == "https://example.com/jobs/5"
    assert unresolved == []


def test_resolve_dlq_unknown_id_exits_nonzero(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    runner.invoke(cli, ["db", "init", "--data-dir", str(tmp_path)])
    result = runner.invoke(
        cli,
        [
            "resolve-dlq",
            "nonexistent-id",
            "--paste-text",
            "some text",
            "--data-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code != 0
