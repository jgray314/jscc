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
    _assert_queued(result)
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


def test_ingest_empty_response_body_dlqs_instead_of_crashing(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gate finding H1, at the boundary where the DoD is actually stated.

    The test below this one mocks `fetch_jd` itself, so it proves the CLI
    handles a FetchResult -- not that the real fetcher always produces one.
    That gap is how an empty-body 200 (a routine bot-block response) reached
    `lxml` and crashed ingest with exit 1 and no DLQ entry. This one drives
    the real fetcher through a mocked transport instead.
    """
    monkeypatch.delenv(ENV_VAR, raising=False)
    runner.invoke(cli, ["db", "init", "--data-dir", str(tmp_path)])

    from unittest.mock import Mock

    empty = Mock()
    empty.status_code = 200
    empty.headers = {}
    empty.encoding = "utf-8"
    empty.is_redirect = False
    empty.iter_content = lambda chunk_size=None: iter([])
    empty.close = Mock()
    # The M5 guards resolve the host before fetching; keep this test offline.
    monkeypatch.setattr("jscc.fetcher._resolve_host", lambda host: ["93.184." + "216.34"])
    monkeypatch.setattr("jscc.fetcher.requests.get", lambda *a, **kw: empty)

    result = runner.invoke(
        cli, ["ingest", "--url", "https://example.com/jobs/9", "--data-dir", str(tmp_path)]
    )

    _assert_queued(result)

    from jscc.mode import Mode
    from jscc.storage import list_applications, list_dlq_entries, open_for_mode

    conn = open_for_mode(Mode.synthetic, tmp_path)
    apps = list_applications(conn)
    entries = list_dlq_entries(conn, unresolved_only=False)
    conn.close()
    assert apps == []
    assert len(entries) == 1
    assert entries[0].failure_mode.value == "extraction_failed"


def test_ingest_converts_a_fetchresult_failure_to_a_dlq_entry(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI-side half of the DoD: a returned FetchResult failure becomes a DLQ
    entry, not an exception. The fetcher-side half is the test above.

    Renamed from `test_ingest_never_crashes_on_fetch_exception_shaped_failure`,
    which overclaimed: it mocks `fetch_jd` itself, so it never tested that the
    fetcher does not crash -- and the fetcher did (H1). It also asserted only
    on the exit code while its docstring described a DLQ entry it never
    queried."""
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
    _assert_queued(result)

    apps, entries = _rows(tmp_path)
    assert apps == []
    assert len(entries) == 1
    assert entries[0].failure_mode.value == "timeout"
    assert entries[0].source_url == "https://example.com/jobs/3"


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
    # "Same shape" has to include the extracted fields, or the assertion is
    # about the wrapper and not the thing the LLM stage produced (H-3).
    assert apps[0].extracted_jd is not None


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


# ---- eval runs are metered (gate finding M1) --------------------------------


def test_eval_command_records_calls_under_its_own_feature_label(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M1: the eval command used to call extract_jd without a conn, so prompt
    iteration — the most token-hungry phase — was the one phase with no cost
    record, while D5 claimed every LLM call was instrumented."""
    monkeypatch.delenv(ENV_VAR, raising=False)
    runner.invoke(cli, ["db", "init", "--data-dir", str(tmp_path)])

    result = runner.invoke(cli, ["eval", "jd_extraction", "--data-dir", str(tmp_path)])
    assert result.exit_code == 1, result.output  # stub fails every case, as expected
    assert "0/15 passed" in result.output

    from jscc.mode import Mode
    from jscc.storage import list_llm_calls, open_for_mode

    conn = open_for_mode(Mode.synthetic, tmp_path)
    calls = list_llm_calls(conn)
    conn.close()

    assert len(calls) == 15, "one ledger row per eval case"
    assert {c.feature for c in calls} == {"extraction_eval"}


def test_ingest_and_eval_traffic_stay_separable_in_the_ledger(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`jscc costs` is a portfolio artifact — eval spend must not inflate the
    per-application figure."""
    monkeypatch.delenv(ENV_VAR, raising=False)
    runner.invoke(cli, ["db", "init", "--data-dir", str(tmp_path)])

    from jscc.fetcher import FetchResult

    monkeypatch.setattr(
        "jscc.cli.fetch_jd",
        lambda url, **kw: FetchResult(ok=True, title="Senior Engineer", raw_text="a" * 300),
    )
    runner.invoke(
        cli, ["ingest", "--url", "https://example.com/jobs/1", "--data-dir", str(tmp_path)]
    )
    runner.invoke(cli, ["eval", "jd_extraction", "--data-dir", str(tmp_path)])

    from jscc.mode import Mode
    from jscc.storage import list_llm_calls, open_for_mode

    conn = open_for_mode(Mode.synthetic, tmp_path)
    features = [c.feature for c in list_llm_calls(conn)]
    conn.close()

    assert features.count("extraction") == 1
    assert features.count("extraction_eval") == 15


def test_extract_jd_rejects_an_unknown_feature_label(tmp_path: Path) -> None:
    """A typo'd label must not silently create a phantom feature in the ledger."""
    from jscc.extraction import extract_jd
    from jscc.mode import Mode
    from jscc.storage import open_for_mode

    conn = open_for_mode(Mode.synthetic, tmp_path)
    try:
        with pytest.raises(ValueError, match="unknown instrumentation feature"):
            extract_jd("some jd text", conn=conn, feature="typo")
    finally:
        conn.close()


# ---- extraction failures reach the DLQ, not a traceback (rerun-gate H-2) ----
#
# B3a's DoD is "produces Application OR DLQEntry, never crashes". H1 restored
# that for the fetch stage; the extraction stage had no route to
# `extraction_failed` at all, so an unparseable model response exited 1 with a
# raw traceback, no Application and nothing to retry from. These tests drive
# the real `extract_jd` through a stubbed client rather than mocking the
# helper, for the reason H-4 exists: mocking the thing under test is how the
# original gap survived a suite that claimed to cover it.


class _CannedClient:
    def __init__(self, text: str, stop_reason: str = "end_turn") -> None:
        self.text = text
        self.stop_reason = stop_reason

    def complete(self, *, model: str, system: str, user: str):
        from jscc.llm_client import LLMResponse

        return LLMResponse(
            text=self.text,
            input_tokens=10,
            output_tokens=5,
            cost_usd=0.001,
            stop_reason=self.stop_reason,
        )


def _ingest_with_client(runner, tmp_path, monkeypatch, client, argv=None):
    monkeypatch.delenv(ENV_VAR, raising=False)
    runner.invoke(cli, ["db", "init", "--data-dir", str(tmp_path)])
    monkeypatch.setattr("jscc.extraction.default_client", lambda: client)
    return runner.invoke(
        cli,
        argv or ["ingest", "--paste", "--company", "C", "--data-dir", str(tmp_path)],
        input="a job description",
    )



def _assert_queued(result) -> None:
    """Handled failure: a DLQ entry was written. Exit 3, and nothing escaped
    as a real exception -- CliRunner reports the SystemExit itself here."""
    assert result.exit_code == 3, result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)


def _rows(tmp_path):
    from jscc.mode import Mode
    from jscc.storage import list_applications, list_dlq_entries, open_for_mode

    conn = open_for_mode(Mode.synthetic, tmp_path)
    try:
        return list_applications(conn), list_dlq_entries(conn, unresolved_only=False)
    finally:
        conn.close()


def test_fenced_json_response_dlqs_instead_of_crashing(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model that prefaces or fences its JSON is the normal case, not an
    edge case -- this is what B2b's first live call is most likely to hit."""
    client = _CannedClient('Here is the JSON:\n```json\n{"title": "X"}\n```')
    result = _ingest_with_client(runner, tmp_path, monkeypatch, client)
    _assert_queued(result)
    apps, entries = _rows(tmp_path)
    assert apps == []
    assert len(entries) == 1
    assert entries[0].failure_mode.value == "extraction_failed"
    assert entries[0].error_detail


def test_truncated_response_is_reported_as_truncation_not_bad_json(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`max_tokens` truncation and a badly-worded prompt produce the same
    JSONDecodeError. Told apart, or prompt iteration chases the wrong bug."""
    client = _CannedClient('{"title": "Staff Engi', stop_reason="max_tokens")
    result = _ingest_with_client(runner, tmp_path, monkeypatch, client)
    _assert_queued(result)
    _apps, entries = _rows(tmp_path)
    assert len(entries) == 1
    assert "truncated" in entries[0].error_detail
    assert "max_tokens" in entries[0].error_detail


def test_valid_json_of_the_wrong_shape_also_dlqs(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other parse-failure mode takes the same route."""
    client = _CannedClient('{"title": "X"}')  # missing required fields
    result = _ingest_with_client(runner, tmp_path, monkeypatch, client)
    _assert_queued(result)
    _apps, entries = _rows(tmp_path)
    assert len(entries) == 1


def test_pasted_dlq_entry_round_trips_through_resolve(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A paste-path failure has no URL, so the entry carries a sentinel.
    `resolve-dlq` must not then try to infer a company from it."""
    from jscc.llm_client import StubExtractionClient

    _ingest_with_client(runner, tmp_path, monkeypatch, _CannedClient("not json"))
    _apps, entries = _rows(tmp_path)
    entry_id = entries[0].id
    assert entries[0].source_url == "(pasted)"

    monkeypatch.setattr("jscc.extraction.default_client", lambda: StubExtractionClient())
    result = runner.invoke(
        cli, ["resolve-dlq", entry_id, "--paste-text", "a jd", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    apps, _entries = _rows(tmp_path)
    assert len(apps) == 1
    assert apps[0].company == "(pasted)"
    assert apps[0].source_url is None


def test_failed_resolve_does_not_create_a_second_dlq_entry(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The existing entry stays unresolved -- that is already the right record.
    A second would duplicate the queue on every retry."""
    _ingest_with_client(runner, tmp_path, monkeypatch, _CannedClient("not json"))
    _apps, entries = _rows(tmp_path)
    result = runner.invoke(
        cli,
        ["resolve-dlq", entries[0].id, "--paste-text", "a jd", "--data-dir", str(tmp_path)],
    )
    _assert_queued(result)
    _apps, after = _rows(tmp_path)
    assert len(after) == 1
    assert after[0].resolution.value == "unresolved"


def test_url_path_extraction_failure_keeps_the_url_on_the_dlq_entry(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The URL path must stay recoverable: the entry has to name what failed."""
    from unittest.mock import Mock

    monkeypatch.delenv(ENV_VAR, raising=False)
    runner.invoke(cli, ["db", "init", "--data-dir", str(tmp_path)])
    monkeypatch.setattr("jscc.fetcher._resolve_host", lambda host: ["93.184." + "216.34"])
    body = ("<html><body><article><p>" + "Senior engineer role. " * 30 + "</p></article></body></html>")
    resp = Mock()
    resp.status_code = 200
    resp.headers = {}
    resp.encoding = "utf-8"
    resp.is_redirect = False
    resp.iter_content = lambda chunk_size=None: iter([body.encode("utf-8")])
    resp.close = Mock()
    monkeypatch.setattr("jscc.fetcher.requests.get", lambda *a, **kw: resp)
    monkeypatch.setattr("jscc.extraction.default_client", lambda: _CannedClient("not json"))

    result = runner.invoke(
        cli, ["ingest", "--url", "https://example.com/jobs/7", "--data-dir", str(tmp_path)]
    )
    _assert_queued(result)
    _apps, entries = _rows(tmp_path)
    assert len(entries) == 1
    assert entries[0].source_url == "https://example.com/jobs/7"
    assert entries[0].failure_mode.value == "extraction_failed"


def test_unpriced_model_is_a_config_error_not_a_dlq_entry(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deliberately different from a parse failure: every ingest would fail
    identically, so queueing them buries the one message worth reading. And
    nothing was billed -- the price check runs before the request."""
    from jscc.llm_client import UnknownModelPricingError

    class _Unpriced:
        def complete(self, *, model: str, system: str, user: str):
            raise UnknownModelPricingError("no rate on file for 'some-model'")

    result = _ingest_with_client(runner, tmp_path, monkeypatch, _Unpriced())
    assert result.exit_code == 2
    apps, entries = _rows(tmp_path)
    assert apps == []
    assert entries == []


# ---- the extraction result is stored, not discarded (rerun-gate H-3) --------


def test_ingest_stores_every_extracted_field_not_just_the_title(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`title` had a production consumer; the other six fields were computed,
    billed, instrumented and dropped. D9's second justification for the
    split-call architecture is that the intermediate output has independent
    product value -- which was false of the code until this landed."""
    import json

    from jscc.models import ExtractedJD

    payload = {
        "title": "Staff Backend Engineer",
        "level": "staff",
        "comp_band": "$200,000-$240,000",
        "location": "Denver, CO",
        "remote_policy": "hybrid",
        "must_have_skills": ["Python", "PostgreSQL"],
        "responsibilities_summary": "Owns the ingestion pipeline.",
    }
    result = _ingest_with_client(runner, tmp_path, monkeypatch, _CannedClient(json.dumps(payload)))
    assert result.exit_code == 0, result.output

    apps, _entries = _rows(tmp_path)
    stored = apps[0].extracted_jd
    assert stored == payload
    # Round-trips back into the model the eval suite grades against.
    assert ExtractedJD(**stored).must_have_skills == ["Python", "PostgreSQL"]


def test_every_extracted_jd_field_survives_storage(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Closes the class rather than the instance: a field added to ExtractedJD
    later must not be silently dropped on the way to the DB, which is the shape
    of bug H-3 was. Mirrors the _GRADED_FIELDS coverage test the eval suite got
    for the same reason (finding H2)."""
    import json

    from jscc.models import ExtractedJD

    payload = {
        "title": "T",
        "level": "senior",
        "comp_band": "band",
        "location": "Denver",
        "remote_policy": "remote",
        "must_have_skills": ["x"],
        "responsibilities_summary": "s",
    }
    assert set(payload) == set(ExtractedJD.model_fields), "test payload is stale"

    _ingest_with_client(runner, tmp_path, monkeypatch, _CannedClient(json.dumps(payload)))
    apps, _entries = _rows(tmp_path)
    assert set(apps[0].extracted_jd) == set(ExtractedJD.model_fields)


def test_resolve_dlq_also_stores_the_extracted_fields(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both writers go through the shared helper; assert it, don't assume it."""
    import json

    from unittest.mock import Mock

    monkeypatch.delenv(ENV_VAR, raising=False)
    runner.invoke(cli, ["db", "init", "--data-dir", str(tmp_path)])
    monkeypatch.setattr("jscc.fetcher._resolve_host", lambda host: ["93.184." + "216.34"])
    blocked = Mock()
    blocked.status_code = 403
    blocked.headers = {}
    blocked.encoding = "utf-8"
    blocked.is_redirect = False
    blocked.iter_content = lambda chunk_size=None: iter([])
    blocked.close = Mock()
    monkeypatch.setattr("jscc.fetcher.requests.get", lambda *a, **kw: blocked)
    runner.invoke(
        cli, ["ingest", "--url", "https://example.com/jobs/3", "--data-dir", str(tmp_path)]
    )
    _apps, entries = _rows(tmp_path)
    assert len(entries) == 1

    payload = {
        "title": "Director of Engineering",
        "level": "director",
        "comp_band": None,
        "location": None,
        "remote_policy": "remote",
        "must_have_skills": [],
        "responsibilities_summary": "Leads the platform org.",
    }
    monkeypatch.setattr(
        "jscc.extraction.default_client", lambda: _CannedClient(json.dumps(payload))
    )
    result = runner.invoke(
        cli,
        ["resolve-dlq", entries[0].id, "--paste-text", "a jd", "--data-dir", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    apps, _entries = _rows(tmp_path)
    assert apps[0].extracted_jd == payload


# ---- exit-code contract ----------------------------------------------------
#
# D6 treats a queued failure as an expected product state, so "the work was
# queued" and "the tool broke" must not share an exit code -- that distinction
# is the DLQ's whole reason for existing. Pinned here because an exit code is
# an interface: it is the one part of a CLI a script depends on and no test
# notices when it changes.


def test_exit_code_constants_match_the_documented_contract() -> None:
    from jscc.cli import EXIT_OK, EXIT_QUEUED, EXIT_UNEXPECTED, EXIT_USAGE

    assert (EXIT_OK, EXIT_UNEXPECTED, EXIT_USAGE, EXIT_QUEUED) == (0, 1, 2, 3)


def test_usage_error_and_queued_failure_are_different_codes(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty input is the caller's mistake and nothing was attempted; a
    parse failure produced a retryable record. Same command, different
    outcomes, so different codes."""
    monkeypatch.delenv(ENV_VAR, raising=False)
    runner.invoke(cli, ["db", "init", "--data-dir", str(tmp_path)])

    empty = runner.invoke(
        cli, ["ingest", "--paste", "--data-dir", str(tmp_path)], input="   \n"
    )
    assert empty.exit_code == 2

    queued = _ingest_with_client(runner, tmp_path, monkeypatch, _CannedClient("not json"))
    assert queued.exit_code == 3


# ---- eval threshold, record and replay -------------------------------------


def _eval_paths(tmp_path, monkeypatch):
    from jscc import evals

    recording = tmp_path / "recorded.json"
    monkeypatch.setattr("jscc.cli.load_recording", lambda: evals.load_recording(recording))
    monkeypatch.setattr("jscc.cli.save_recording", lambda r: evals.save_recording(r, recording))
    return recording


def test_eval_fails_below_the_threshold_and_says_the_number(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bar used to live only in README prose, while the command actually
    failed if *any* case failed -- a different rule from the one claimed."""
    monkeypatch.delenv(ENV_VAR, raising=False)
    runner.invoke(cli, ["db", "init", "--data-dir", str(tmp_path)])
    result = runner.invoke(cli, ["eval", "jd_extraction", "--data-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "80%" in result.output


def test_eval_passes_when_the_bar_is_lowered(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    runner.invoke(cli, ["db", "init", "--data-dir", str(tmp_path)])
    result = runner.invoke(
        cli,
        ["eval", "jd_extraction", "--min-pass-rate", "0.0", "--data-dir", str(tmp_path)],
    )
    assert result.exit_code == 0


def test_record_then_replay_round_trips(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replay is what lets CI gate the suite with no key and no spend."""
    import json

    monkeypatch.delenv(ENV_VAR, raising=False)
    runner.invoke(cli, ["db", "init", "--data-dir", str(tmp_path)])
    recording = _eval_paths(tmp_path, monkeypatch)

    rec = runner.invoke(
        cli,
        ["eval", "jd_extraction", "--record", "--min-pass-rate", "0.0", "--data-dir", str(tmp_path)],
    )
    assert rec.exit_code == 0, rec.output
    assert recording.exists()
    assert len(json.loads(recording.read_text(encoding="utf-8"))) == 15

    play = runner.invoke(
        cli,
        ["eval", "jd_extraction", "--replay", "--min-pass-rate", "0.0", "--data-dir", str(tmp_path)],
    )
    assert play.exit_code == 0, play.output
    assert "15" in play.output


def test_replay_without_a_recording_is_a_usage_error(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    runner.invoke(cli, ["db", "init", "--data-dir", str(tmp_path)])
    _eval_paths(tmp_path, monkeypatch)
    result = runner.invoke(
        cli, ["eval", "jd_extraction", "--replay", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code == 2


def test_record_and_replay_are_mutually_exclusive(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    runner.invoke(cli, ["db", "init", "--data-dir", str(tmp_path)])
    result = runner.invoke(
        cli, ["eval", "jd_extraction", "--record", "--replay", "--data-dir", str(tmp_path)]
    )
    assert result.exit_code != 0


def test_replay_refuses_a_prompt_it_has_no_recording_for(tmp_path: Path) -> None:
    """Keyed on the prompt, not the case id, so a recording cannot keep
    replaying after the prompt or the redaction rules moved underneath it."""
    from jscc.evals import RecordingMissing, ReplayClient

    client = ReplayClient({"some-other-hash": '{"title": "X"}'})
    with pytest.raises(RecordingMissing):
        client.complete(model="m", system="s", user="a prompt never recorded")
