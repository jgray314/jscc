from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from jscc.cli import cli
from jscc.followup import followup, format_briefing, render_briefing
from jscc.mode import ENV_VAR
from jscc.models import (
    Application,
    Briefing,
    DraftEmail,
    Interaction,
    RoutingClassification,
    RoutingDecision,
)


def _app() -> Application:
    return Application(
        id="app-1",
        source_url="https://example.com/jobs/1",
        title="Engineering Manager",
        company="Acme",
        stage="interviewing",
    )


def _history() -> list[Interaction]:
    return [
        Interaction(
            application_id="app-1",
            type="screen",
            occurred_at="2026-09-01T10:00:00Z",
            notes="Recruiter screen.",
        )
    ]


_ROUTINE = RoutingDecision(classification=RoutingClassification.routine, intent="cadence_nudge")
_NON_ROUTINE = RoutingDecision(
    classification=RoutingClassification.non_routine,
    reason="Rejection with an invitation to stay in touch.",
    considerations=["Whether to ask for feedback", "Keep the door open"],
)


class _Spy:
    def __init__(self, result):
        self.result = result
        self.calls: list[tuple] = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


# ---- renderer -----------------------------------------------------------------


def test_render_briefing_populates_every_field() -> None:
    b = render_briefing(_app(), _NON_ROUTINE)
    assert b.application_id == "app-1"
    assert (b.company, b.title, b.stage) == ("Acme", "Engineering Manager", "interviewing")
    assert b.source_url == "https://example.com/jobs/1"
    assert b.reason == _NON_ROUTINE.reason
    assert b.considerations == _NON_ROUTINE.considerations
    assert b.handle_manually is True


def test_render_briefing_refuses_a_routine_decision() -> None:
    with pytest.raises(ValueError, match="non_routine"):
        render_briefing(_app(), _ROUTINE)


def test_render_briefing_names_a_missing_reason_instead_of_printing_none() -> None:
    bare = RoutingDecision(classification=RoutingClassification.non_routine)
    b = render_briefing(_app(), bare)
    assert "None" not in b.reason
    assert b.reason


def test_format_briefing_is_readable_and_links_the_source() -> None:
    text = format_briefing(render_briefing(_app(), _NON_ROUTINE))
    assert "HANDLE MANUALLY" in text
    assert "Acme" in text and "Engineering Manager" in text
    assert "Rejection with an invitation to stay in touch." in text
    assert "  - Whether to ask for feedback" in text
    assert "app-1" in text
    assert "https://example.com/jobs/1" in text


def test_format_briefing_omits_absent_optional_lines() -> None:
    app = _app().model_copy(update={"source_url": None})
    bare = RoutingDecision(classification=RoutingClassification.non_routine, reason="Ambiguous.")
    text = format_briefing(render_briefing(app, bare))
    assert "Posting:" not in text
    assert "Weigh before replying" not in text


# ---- orchestrator -------------------------------------------------------------


def test_routine_returns_a_draft_composed_from_the_routed_intent() -> None:
    draft = DraftEmail(subject="Checking in", body="Hi -- any update?")
    composer = _Spy(draft)
    result = followup(_app(), _history(), ["sample one"], router=_Spy(_ROUTINE), composer=composer)
    assert result == draft
    ((args, _),) = composer.calls
    assert args[2] == "cadence_nudge"
    assert args[3] == ["sample one"]


def test_non_routine_returns_a_briefing_and_never_composes() -> None:
    composer = _Spy(DraftEmail(subject="x", body="y"))
    result = followup(_app(), _history(), [], router=_Spy(_NON_ROUTINE), composer=composer)
    assert isinstance(result, Briefing)
    assert result.reason == _NON_ROUTINE.reason
    assert composer.calls == []


def test_routine_without_an_intent_falls_back_to_a_briefing() -> None:
    composer = _Spy(DraftEmail(subject="x", body="y"))
    intentless = RoutingDecision(classification=RoutingClassification.routine)
    result = followup(_app(), _history(), [], router=_Spy(intentless), composer=composer)
    assert isinstance(result, Briefing)
    assert composer.calls == []


def test_conn_is_forwarded_to_the_router() -> None:
    router = _Spy(_NON_ROUTINE)
    followup(_app(), _history(), [], conn="sentinel", router=router)
    assert router.calls[0][1]["conn"] == "sentinel"


# ---- CLI ----------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _ingest(runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.delenv(ENV_VAR, raising=False)
    runner.invoke(cli, ["db", "init", "--data-dir", str(tmp_path)])
    result = runner.invoke(
        cli,
        ["ingest", "--paste", "--company", "TestCo", "--data-dir", str(tmp_path)],
        input="Senior Engineering Manager. Remote.",
    )
    assert result.exit_code == 0, result.output
    return result.output.split(": ", 1)[0].rsplit(" ", 1)[-1]


def test_cli_unknown_application_is_a_usage_error(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    runner.invoke(cli, ["db", "init", "--data-dir", str(tmp_path)])
    result = runner.invoke(cli, ["followup", "nope", "--data-dir", str(tmp_path)])
    assert result.exit_code == 2
    assert "no application found" in result.output


def test_cli_non_routine_prints_a_briefing(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stub router always answers non_routine, so no API key still yields a card."""
    app_id = _ingest(runner, tmp_path, monkeypatch)
    result = runner.invoke(cli, ["followup", app_id, "--data-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "HANDLE MANUALLY" in result.output
    assert app_id in result.output


def test_cli_routine_prints_the_draft(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_id = _ingest(runner, tmp_path, monkeypatch)
    monkeypatch.setattr(
        "jscc.cli.agents.followup",
        lambda app, history, samples, conn=None: DraftEmail(subject="Hello", body="Body text"),
    )
    result = runner.invoke(cli, ["followup", app_id, "--data-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "Subject: Hello" in result.output
    assert "Body text" in result.output


def test_cli_routing_call_lands_in_the_routing_ledger(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_id = _ingest(runner, tmp_path, monkeypatch)
    runner.invoke(cli, ["followup", app_id, "--data-dir", str(tmp_path)])

    from jscc.mode import Mode
    from jscc.storage import list_llm_calls, open_for_mode

    conn = open_for_mode(Mode.synthetic, tmp_path)
    features = [c.feature for c in list_llm_calls(conn)]
    conn.close()
    assert features.count("routing") == 1


# ---- composer needs_input -> briefing (D4c) ---------------------------------------


def test_a_composer_needs_input_becomes_a_briefing_naming_the_missing_detail() -> None:
    asked = DraftEmail(needs_input="Dietary needs for the onsite lunch.")
    result = followup(_app(), _history(), [], router=_Spy(_ROUTINE), composer=_Spy(asked))
    assert isinstance(result, Briefing)
    assert result.handle_manually
    assert "Dietary needs for the onsite lunch." in result.reason
    assert result.application_id == "app-1" and result.company == "Acme"
    assert "Dietary needs" in format_briefing(result)


def test_followup_forwards_conn_to_the_composer() -> None:
    composer = _Spy(DraftEmail(subject="x", body="y"))
    sentinel = object()
    followup(_app(), _history(), [], conn=sentinel, router=_Spy(_ROUTINE), composer=composer)
    ((_, kwargs),) = composer.calls
    assert kwargs.get("conn") is sentinel


def test_a_real_composer_call_from_followup_lands_in_the_composition_ledger(tmp_path) -> None:
    from jscc.composition import compose_followup
    from jscc.llm_client import LLMResponse
    from jscc.storage import _connect, _init_db, list_llm_calls

    class _Client:
        def complete(self, *, model, system, user):
            return LLMResponse(
                text='{"subject": "Hi", "body": "Thanks."}',
                input_tokens=1,
                output_tokens=1,
                cost_usd=0.0,
            )

    conn = _connect(tmp_path / "t.db")
    _init_db(conn)
    followup(
        _app(),
        _history(),
        [],
        conn=conn,
        router=_Spy(_ROUTINE),
        composer=lambda app, history, intent, samples, conn=None: compose_followup(
            app, history, intent, samples, conn=conn, client=_Client()
        ),
    )
    assert [c.feature for c in list_llm_calls(conn)] == ["composition"]
    conn.close()


def test_cli_needs_input_prints_a_briefing(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_id = _ingest(runner, tmp_path, monkeypatch)
    # followup's router/composer defaults are bound at definition time, so
    # patching the module attributes wouldn't reach them; inject instead.
    real = followup
    monkeypatch.setattr(
        "jscc.cli.agents.followup",
        lambda app, history, samples, conn=None: real(
            app,
            history,
            samples,
            conn=conn,
            router=lambda *a, **k: _ROUTINE,
            composer=lambda *a, **k: DraftEmail(needs_input="Which of the three slots works?"),
        ),
    )
    result = runner.invoke(cli, ["followup", app_id, "--data-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "HANDLE MANUALLY" in result.output
    assert "Which of the three slots works?" in result.output


def test_cli_reports_a_composer_parse_error_without_a_traceback(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jscc.composition import CompositionParseError

    app_id = _ingest(runner, tmp_path, monkeypatch)

    def boom(app, history, samples, conn=None):
        raise CompositionParseError("composition response was not valid JSON")

    monkeypatch.setattr("jscc.cli.agents.followup", boom)
    result = runner.invoke(cli, ["followup", app_id, "--data-dir", str(tmp_path)])
    assert result.exit_code != 0
    assert "drafting failed" in result.output
