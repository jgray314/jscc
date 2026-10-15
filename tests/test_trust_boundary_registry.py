"""Every field a client can write at an entry point, and what ties it to the server.

The confirm-resubmit flow shipped twice with the same flaw one field over: first
a client-supplied extraction blob was trusted as "what extraction said", then,
after that was fixed, the client-supplied `paste_text` was paired with a stored
extraction nothing tied it to. Both were found by a cold review. The shape is
general: a value arrives from outside, and some other value the server holds is
assumed to correspond to it.

This is one table. The web surface is discovered from the app's own routes and the
CLI surface from click's command tree, so a new form field or option fails here
until it has a row. A row names how the field is tied to server state and carries
a check that drives the entry point and observes the behavior:

- server_state: the field only selects a record the server already holds.
- revalidated: the value is checked, or re-derived, before anything is stored.
- override: a human-authored correction, stored where an override belongs and
  never in the field that records what the model said.
- read_only: the field changes what is shown, writes nothing.
- operator: chooses a local path or mode on the operator's own machine.

"""

from __future__ import annotations

import ast
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import click
import pytest
from click.testing import CliRunner
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from jscc.cli import cli
from jscc.fetcher import FetchResult
from jscc.ingest_logic import MAX_PASTE_BYTES
from jscc.llm_client import LLMResponse
from jscc.mode import ENV_VAR, Mode
from jscc.models import Application, DLQEntry, FailureMode, Resolution
from jscc.storage import (
    create_dlq_entry,
    list_applications,
    list_dlq_entries,
    open_for_mode,
)
from jscc.web import create_app

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "config"
PASTE = "a jd with no title in it " * 15
TRUE_TITLE = "Staff Backend Engineer"
# One byte past the ceiling shared with the fetcher.
OVERSIZE = "a " * (MAX_PASTE_BYTES // 2 + 1)


class _Extractor:
    """A model client whose title never appears in PASTE, so verification flags it."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *, model: str, system: str, user: str) -> LLMResponse:
        self.calls += 1
        payload = {
            "title": TRUE_TITLE,
            "company": None,
            "level": "staff",
            "comp_band": None,
            "location": None,
            "remote_policy": None,
            "must_have_skills": [],
            "responsibilities_summary": "s",
        }
        return LLMResponse(
            text=json.dumps(payload),
            input_tokens=10,
            output_tokens=5,
            cost_usd=0.001,
            stop_reason="end_turn",
        )


@dataclass
class Env:
    data_dir: Path
    entry_id: str
    extractor: _Extractor

    def web(self) -> TestClient:
        app = create_app(data_dir=self.data_dir, config_dir=CONFIG_DIR)
        return TestClient(app, base_url="http://localhost")

    def apps(self) -> list[Application]:
        conn = open_for_mode(Mode.synthetic, self.data_dir)
        try:
            return list_applications(conn)
        finally:
            conn.close()

    def entry(self) -> DLQEntry:
        conn = open_for_mode(Mode.synthetic, self.data_dir)
        try:
            entries = list_dlq_entries(conn, unresolved_only=False)
        finally:
            conn.close()
        return next(e for e in entries if e.id == self.entry_id)

    def cli(self, *args: str, input: str | None = None):
        argv = [*args, "--data-dir", str(self.data_dir)]
        return CliRunner().invoke(cli, argv, input=input)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    monkeypatch.delenv(ENV_VAR, raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    conn = open_for_mode(Mode.synthetic, tmp_path)
    try:
        entry_id = create_dlq_entry(
            conn,
            DLQEntry(
                source_url="https://example.com/jobs/5",
                failure_mode=FailureMode.blocked,
                error_detail="HTTP 403",
            ),
        )
    finally:
        conn.close()
    extractor = _Extractor()
    monkeypatch.setattr("jscc.extraction.default_client", lambda: extractor)
    monkeypatch.setattr(
        "jscc.cli.ingest.fetch_jd",
        lambda url, **kw: FetchResult(ok=True, title="x", raw_text=PASTE),
    )
    return Env(tmp_path, entry_id, extractor)


# ---- web checks ---------------------------------------------------------------------


def _resolve_url(env: Env) -> str:
    return f"/dlq/{env.entry_id}/resolve"


def web_now_is_parsed_or_refused(env: Env) -> None:
    client = env.web()
    assert client.get("/?now=not-a-time").status_code == 400
    assert client.get("/?now=2026-08-28T12:00:00").status_code == 400  # no offset
    assert client.get("/?now=2026-08-28T12:00:00%2B00:00").status_code == 200


def web_app_id_selects_a_stored_row(env: Env) -> None:
    assert env.web().get("/applications/no-such-application").status_code == 404


def web_all_filters_the_view_and_writes_nothing(env: Env) -> None:
    client = env.web()
    before = env.entry()
    assert client.get("/dlq").status_code == 200
    assert client.get("/dlq?all=true").status_code == 200
    assert env.entry() == before
    assert env.apps() == []


def web_entry_id_selects_a_stored_entry(env: Env) -> None:
    client = env.web()
    assert client.get("/dlq/no-such-entry/resolve").status_code == 404
    response = client.post("/dlq/no-such-entry/resolve", data={"paste_text": PASTE})
    assert response.status_code == 404
    assert env.apps() == []
    assert env.extractor.calls == 0


def web_paste_text_is_extracted_verified_and_paired(env: Env) -> None:
    client = env.web()
    assert client.post(_resolve_url(env), data={"paste_text": "   "}).status_code == 400
    # A body past the form parser's per-field ceiling is refused before extraction.
    assert client.post(_resolve_url(env), data={"paste_text": OVERSIZE}).status_code in (400, 413)
    assert env.extractor.calls == 0

    first = client.post(_resolve_url(env), data={"paste_text": PASTE})
    assert "not found verbatim" in first.text
    assert env.apps() == []
    assert env.entry().resolution == Resolution.unresolved

    other = "an entirely unrelated second posting's text"
    second = client.post(
        _resolve_url(env),
        data={"paste_text": other, "title": "Corrected", "confirming": "1"},
    )
    assert second.status_code == 200
    # The stored extraction belonged to the first text, so it was not reused.
    assert env.extractor.calls == 2
    [created] = env.apps()
    assert created.source_raw == other


def web_override_lands_on_the_application_only(env: Env) -> None:
    client = env.web()
    client.post(_resolve_url(env), data={"paste_text": PASTE})
    response = client.post(
        _resolve_url(env),
        data={"paste_text": PASTE, "title": "Corrected", "company": "Acme", "confirming": "1"},
    )
    assert response.status_code == 200
    [created] = env.apps()
    assert (created.title, created.company) == ("Corrected", "Acme")
    assert created.extracted_jd["title"] == TRUE_TITLE
    # And a confirmed title may not be blank.
    blank = client.post(
        _resolve_url(env), data={"paste_text": PASTE, "title": "  ", "confirming": "1"}
    )
    assert blank.status_code == 400


def web_confirming_cannot_assert_what_extraction_said(env: Env) -> None:
    response = env.web().post(
        _resolve_url(env),
        data={
            "paste_text": PASTE,
            "title": "Attacker Title",
            "confirming": "1",
            # Not a declared field. It must be ignored, not stored.
            "extracted_json": json.dumps({"title": "Fabricated", "level": "vp"}),
        },
    )
    assert response.status_code == 200
    [created] = env.apps()
    assert created.extracted_jd["title"] == TRUE_TITLE
    assert created.extracted_jd["level"] == "staff"
    assert env.extractor.calls == 1


# ---- CLI checks ---------------------------------------------------------------------


def cli_url_is_refused_by_the_fetcher_not_trusted(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.undo()  # the real fetcher, which refuses these before any request
    monkeypatch.delenv(ENV_VAR, raising=False)
    for url in ("ftp://example.com/job", "http://127.0.0.1:9/job"):
        result = env.cli("ingest", "--url", url)
        assert result.exit_code != 0, url
    assert env.apps() == []


def cli_paste_text_is_verified_before_it_is_stored(env: Env) -> None:
    cli_stdin_paste_over_the_ceiling_is_refused_before_any_work(env)
    result = env.cli("ingest", "--paste", input=PASTE)
    assert result.exit_code == 2, result.output  # needs confirmation, stdin is spent
    assert env.apps() == []


def cli_file_is_a_real_file_and_is_verified(env: Env) -> None:
    cli_file_over_the_ceiling_is_refused_before_any_work(env)
    missing = env.cli("ingest", "--file", str(env.data_dir / "absent.txt"))
    assert missing.exit_code != 0
    jd = env.data_dir / "jd.txt"
    jd.write_text(PASTE, encoding="utf-8")
    result = env.cli("ingest", "--file", str(jd), input="Corrected\n\n")
    assert result.exit_code == 0, result.output
    [created] = env.apps()
    assert created.title == "Corrected"
    assert created.extracted_jd["title"] == TRUE_TITLE


def cli_override_lands_on_the_application_only(env: Env) -> None:
    result = env.cli("ingest", "--paste", "--title", "Mine", "--company", "Acme", input=PASTE)
    assert result.exit_code == 0, result.output
    [created] = env.apps()
    assert (created.title, created.company) == ("Mine", "Acme")
    assert created.extracted_jd["title"] == TRUE_TITLE


def cli_update_can_only_reach_the_duplicate_the_server_found(env: Env) -> None:
    first = env.cli("ingest", "--paste", "--title", "Mine", "--company", "Acme", input=PASTE)
    assert first.exit_code == 0, first.output
    second = env.cli(
        "ingest", "--paste", "--title", "Mine", "--company", "Acme", "--update", input=PASTE
    )
    assert second.exit_code == 0, second.output
    assert len(env.apps()) == 1
    other = env.cli(
        "ingest", "--paste", "--title", "Mine", "--company", "Acme", "--update", input=PASTE + "!"
    )
    assert other.exit_code == 0, other.output
    assert len(env.apps()) == 2  # a different text is a different row, never a rewrite


def cli_mode_marker_decides_which_database_opens(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_VAR, "real")
    result = env.cli("ingest", "--paste", "--title", "t", "--company", "c", input=PASTE)
    assert result.exit_code != 0  # synthetic.db is not a real-mode database
    assert env.apps() == []


def cli_flag_changes_what_is_listed_and_writes_nothing(env: Env) -> None:
    before = env.entry()
    assert env.cli("dlq", "list").exit_code == 0
    assert env.cli("dlq", "list", "--all").exit_code == 0
    assert env.entry() == before


def cli_entry_id_selects_a_stored_entry(env: Env) -> None:
    result = env.cli("resolve-dlq", "no-such-entry", "--paste-text", PASTE)
    assert result.exit_code == 2, result.output
    assert env.apps() == []


def cli_resolve_paste_text_is_verified_before_it_is_stored(env: Env) -> None:
    result = env.cli("resolve-dlq", env.entry_id, "--paste-text", PASTE, input="Corrected\n\n")
    assert result.exit_code == 0, result.output
    [created] = env.apps()
    assert created.title == "Corrected"
    assert created.extracted_jd["title"] == TRUE_TITLE
    assert env.entry().resolution == Resolution.manual_paste


def cli_resolve_override_lands_on_the_application_only(env: Env) -> None:
    result = env.cli(
        "resolve-dlq", env.entry_id, "--paste-text", PASTE, "--title", "Mine", "--company", "Acme"
    )
    assert result.exit_code == 0, result.output
    [created] = env.apps()
    assert (created.title, created.company) == ("Mine", "Acme")
    assert created.extracted_jd["title"] == TRUE_TITLE


def cli_stdin_paste_over_the_ceiling_is_refused_before_any_work(env: Env) -> None:
    result = env.cli("ingest", "--paste", "--title", "t", "--company", "c", input=OVERSIZE)
    assert result.exit_code == 2, result.output
    assert "5 MB" in result.output
    assert env.extractor.calls == 0
    assert env.apps() == []


def cli_file_over_the_ceiling_is_refused_before_any_work(env: Env) -> None:
    big = env.data_dir / "big.txt"
    big.write_text(OVERSIZE, encoding="utf-8")
    result = env.cli("ingest", "--file", str(big), "--title", "t", "--company", "c")
    assert result.exit_code == 2, result.output
    assert "5 MB" in result.output
    assert env.extractor.calls == 0
    assert env.apps() == []


# ---- the registry -------------------------------------------------------------------


@dataclass(frozen=True)
class Row:
    surface: str
    where: str
    field: str
    tie: str
    check: Callable[..., None]


def _web(where: str, field: str, tie: str, check, **kw) -> Row:
    return Row("web", where, field, tie, check, **kw)


def _cli(where: str, field: str, tie: str, check, **kw) -> Row:
    return Row("cli", where, field, tie, check, **kw)


_RESOLVE = "POST /dlq/{entry_id}/resolve"

REGISTRY: list[Row] = [
    _web("GET /", "now", "revalidated", web_now_is_parsed_or_refused),
    _web("GET /applications/{app_id}", "app_id", "server_state", web_app_id_selects_a_stored_row),
    _web("GET /dlq", "all", "read_only", web_all_filters_the_view_and_writes_nothing),
    _web(
        "GET /dlq/{entry_id}/resolve",
        "entry_id",
        "server_state",
        web_entry_id_selects_a_stored_entry,
    ),
    _web(_RESOLVE, "entry_id", "server_state", web_entry_id_selects_a_stored_entry),
    _web(
        _RESOLVE,
        "paste_text",
        "revalidated",
        web_paste_text_is_extracted_verified_and_paired,
    ),
    _web(_RESOLVE, "title", "override", web_override_lands_on_the_application_only),
    _web(_RESOLVE, "company", "override", web_override_lands_on_the_application_only),
    _web(_RESOLVE, "confirming", "server_state", web_confirming_cannot_assert_what_extraction_said),
    _cli("ingest", "--url", "revalidated", cli_url_is_refused_by_the_fetcher_not_trusted),
    _cli(
        "ingest",
        "--paste",
        "revalidated",
        cli_paste_text_is_verified_before_it_is_stored,
    ),
    _cli(
        "ingest",
        "--file",
        "revalidated",
        cli_file_is_a_real_file_and_is_verified,
    ),
    _cli("ingest", "--company", "override", cli_override_lands_on_the_application_only),
    _cli("ingest", "--title", "override", cli_override_lands_on_the_application_only),
    _cli(
        "ingest",
        "--update",
        "server_state",
        cli_update_can_only_reach_the_duplicate_the_server_found,
    ),
    _cli("ingest", "--data-dir", "operator", cli_mode_marker_decides_which_database_opens),
    _cli("ingest", "--config-dir", "operator", cli_mode_marker_decides_which_database_opens),
    _cli("dlq list", "--all", "read_only", cli_flag_changes_what_is_listed_and_writes_nothing),
    _cli("dlq list", "--data-dir", "operator", cli_mode_marker_decides_which_database_opens),
    _cli("resolve-dlq", "entry_id", "server_state", cli_entry_id_selects_a_stored_entry),
    _cli(
        "resolve-dlq",
        "--paste-text",
        "revalidated",
        cli_resolve_paste_text_is_verified_before_it_is_stored,
    ),
    _cli(
        "resolve-dlq", "--company", "override", cli_resolve_override_lands_on_the_application_only
    ),
    _cli("resolve-dlq", "--title", "override", cli_resolve_override_lands_on_the_application_only),
    _cli("resolve-dlq", "--data-dir", "operator", cli_mode_marker_decides_which_database_opens),
]

# CLI commands outside the ingest module. Each takes only an application id the
# database must already hold, or a path or number the operator chose; none accepts
# posting text or anything a browser can send.
CLI_OUT_OF_SCOPE = {
    "costs": "reads the ledger; --data-dir only",
    "db init": "creates the mode-stamped database; --data-dir only",
    "eval composition": "operator paths and flags over recorded fixtures",
    "eval extraction_to_scoring": "operator paths and flags over recorded fixtures",
    "eval fit_scoring": "operator paths and flags over recorded fixtures",
    "eval jd_extraction": "operator paths and flags over recorded fixtures",
    "eval routing": "operator paths and flags over recorded fixtures",
    "followup": "application id looked up in the database",
    "report": "read-only; --now pins the clock",
    "route": "application id looked up in the database",
    "score": "application id looked up in the database",
    "seed": "writes synthetic rows only; refuses real mode",
    "serve": "binds the dashboard; its fields are the web rows above",
    "validate-config": "reads config files",
}


# ---- discovery ----------------------------------------------------------------------


def _dependant_fields(dependant) -> set[str]:
    names: set[str] = set()
    for params in (
        dependant.path_params,
        dependant.query_params,
        dependant.header_params,
        dependant.cookie_params,
        dependant.body_params,
    ):
        names |= {p.alias or p.name for p in params}
    for sub in dependant.dependencies:
        names |= _dependant_fields(sub)
    return names


def web_surface(tmp_path: Path) -> set[tuple[str, str, str]]:
    app = create_app(data_dir=tmp_path, config_dir=CONFIG_DIR)
    found = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            for name in _dependant_fields(route.dependant):
                found.add(("web", f"{method} {route.path}", name))
    return found


def cli_commands() -> dict[str, click.Command]:
    found: dict[str, click.Command] = {}

    def walk(group: click.Group, prefix: str) -> None:
        for name, command in group.commands.items():
            path = f"{prefix} {name}".strip()
            if isinstance(command, click.Group):
                walk(command, path)
            else:
                found[path] = command

    walk(cli, "")
    return found


def cli_surface() -> set[tuple[str, str, str]]:
    found = set()
    for path, command in cli_commands().items():
        if command.callback.__module__ != "jscc.cli.ingest":
            continue
        for param in command.params:
            name = param.opts[0] if isinstance(param, click.Option) else param.name
            found.add(("cli", path, name))
    return found


def test_every_client_writable_field_has_a_row(tmp_path: Path) -> None:
    discovered = web_surface(tmp_path) | cli_surface()
    registered = {(r.surface, r.where, r.field) for r in REGISTRY}
    assert discovered - registered == set(), (
        "a client-writable field has no row in REGISTRY; say how it is tied to "
        "server state and add a check"
    )
    assert registered - discovered == set(), "a registry row names a field that no longer exists"


def test_every_cli_command_is_registered_or_explicitly_out_of_scope() -> None:
    ingest_module = {
        p for p, c in cli_commands().items() if c.callback.__module__ == "jscc.cli.ingest"
    }
    others = set(cli_commands()) - ingest_module
    assert others == set(CLI_OUT_OF_SCOPE), (
        "a CLI command is neither in the ingest module's registered rows nor in "
        f"CLI_OUT_OF_SCOPE with a reason: {sorted(others ^ set(CLI_OUT_OF_SCOPE))}"
    )


def test_no_route_hands_the_trusted_replay_argument_to_the_resolver() -> None:
    """`extracted_override` is the CLI's same-process replay. The dashboard may only
    pass the marker that makes the server look up its own record."""
    tree = ast.parse((REPO_ROOT / "jscc" / "web" / "app.py").read_text(encoding="utf-8"))
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and getattr(n.func, "id", getattr(n.func, "attr", "")) == "resolve_dlq_entry_via_paste"
    ]
    assert calls, "the dashboard no longer calls the resolver; update this test"
    for call in calls:
        keywords = {k.arg for k in call.keywords}
        assert "extracted_override" not in keywords
        assert keywords <= {"company", "title", "refuse_stub", "use_pending_extraction"}, keywords


def _id(row: Row) -> str:
    return f"{row.surface}:{row.where}:{row.field}"


@pytest.mark.parametrize("row", REGISTRY, ids=_id)
def test_each_field_is_tied_to_server_state_as_its_row_says(
    row: Row, env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    params = row.check.__code__.co_varnames[: row.check.__code__.co_argcount]
    if "monkeypatch" in params:
        row.check(env, monkeypatch)
    else:
        row.check(env)
