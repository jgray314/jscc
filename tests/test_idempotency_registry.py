"""Every operation that writes state or re-runs on retry, and what makes a retry safe.

The idempotency guard was built one operation at a time. `ingest` and
`resolve-dlq` got duplicate detection; a later review found the DLQ resolve still
duplicated under a race, which needed a compare-and-set and a per-entry lock. The shape is general: a write that is safe once is not
automatically safe when the same command is run again after a timeout, a crash
between two writes, or a second tab.

Discovery is an AST scan of the `jscc/` package. It records, as
`(file, enclosing function, what)`:

- `sql`            a function that holds a mutating SQL statement;
- `file:<method>`  a function that writes, creates or removes a file;
- `memory:<name>`  a function that mutates module-level state the process keeps
                   between calls;
- `call:<name>`    a call to one of the state-writing functions above.

Every site has a row naming the operation, its idempotency property, and a check
that runs the operation twice (or racing) and observes the state. The
properties:

- natural_key:      a retry finds the record by a key the server holds and
                    updates or skips it; it never creates a second one.
- compare_and_set:  the write is conditional on the state the caller read; of two
                    racers one wins and the other is told.
- same_state:       the stored values are a function of the inputs, so a second
                    run leaves the same state (bookkeeping stamps aside).
- schema_stamp:     DDL guarded by IF NOT EXISTS, or a stamp that is replaced.
- merge_only:       a retry can add or update keys; it cannot lose one.
- reset_rebuild:    clears what it owns, then writes a deterministic fixture.
- append_per_attempt: each call adds a row by design (a ledger of attempts).
- consume_once:     the state is single-use; a replay finds nothing.
- serialized:       overlapping callers for the same key are run one at a time.
- caller_keyed:     the primitive is a plain write; the row for each call site
                    says what makes that call safe to repeat.

Deliberately not covered: SQLite's own durability, writes made by a dependency,
and everything under `scripts/` (operator tooling that writes working files it
owns: the feature ledger has its own tamper check, and the capture scripts reach
recorded fixtures only through `save_recording`, which has a row). Whether a
retry is *wanted* is a product call; this file pins what happens.
"""

from __future__ import annotations

import ast
import json
import re
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from jscc import dlq, evals, instrumentation, storage
from jscc.cli import cli
from jscc.fetcher import FetchResult
from jscc.llm_client import LLMResponse
from jscc.mode import ENV_VAR, Mode
from jscc.models import (
    Application,
    Contact,
    ContactRole,
    DLQEntry,
    FailureMode,
    Interaction,
    InteractionType,
    LLMCallRecord,
    Resolution,
)
from jscc.seed import seed_synthetic

REPO_ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("jscc",)
SKIP_FILES: frozenset[str] = frozenset()

_MUTATING_SQL = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE\s+FROM|CREATE\s+TABLE|ALTER\s+TABLE|DROP\s+TABLE|REPLACE\s+INTO)\b",
    re.I,
)
_FILE_WRITERS = frozenset({"write_text", "write_bytes", "mkdir", "unlink", "rename", "touch"})
_MEMORY_MUTATORS = frozenset({"pop", "popitem", "setdefault", "clear", "update"})

Site = tuple[str, str, str]


# ---- discovery ----------------------------------------------------------------------


def _is_docstring(node: ast.AST, parent: ast.AST | None) -> bool:
    return isinstance(parent, ast.Expr)


def _scopes(tree: ast.Module):
    """Yield (qualified function name, node) for every node, `<module>` at top."""

    def walk(node: ast.AST, scope: tuple[str, ...], parent: ast.AST | None):
        yield ".".join(scope) or "<module>", node, parent
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                yield from walk(child, (*scope, child.name), node)
            else:
                yield from walk(child, scope, node)

    yield from walk(tree, (), None)


def _module_level_mutables(tree: ast.Module) -> set[str]:
    names = set()
    for node in tree.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        is_container = isinstance(value, ast.Dict | ast.List | ast.Set) or (
            isinstance(value, ast.Call) and getattr(value.func, "id", "") in ("dict", "set", "list")
        )
        if is_container:
            names |= {t.id for t in targets if isinstance(t, ast.Name)}
    return names


def _primitive_sites(rel: str, source: str) -> set[Site]:
    tree = ast.parse(source, filename=rel)
    mutables = _module_level_mutables(tree)
    found: set[Site] = set()
    for scope, node, parent in _scopes(tree):
        if scope == "<module>":
            continue
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and not _is_docstring(node, parent)
            and _MUTATING_SQL.match(node.value)
        ):
            found.add((rel, scope, "sql"))
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in _FILE_WRITERS:
                found.add((rel, scope, f"file:{func.attr}"))
            elif (
                isinstance(func, ast.Name)
                and func.id == "open"
                and len(node.args) > 1
                and isinstance(node.args[1], ast.Constant)
                and set(str(node.args[1].value)) & set("wax+")
            ):
                found.add((rel, scope, "file:open"))
            if (
                isinstance(func, ast.Attribute)
                and func.attr in _MEMORY_MUTATORS
                and isinstance(func.value, ast.Name)
                and func.value.id in mutables
            ):
                found.add((rel, scope, f"memory:{func.value.id}"))
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.ctx, ast.Store | ast.Del)
            and isinstance(node.value, ast.Name)
            and node.value.id in mutables
        ):
            found.add((rel, scope, f"memory:{node.value.id}"))
    return found


def _call_sites(rel: str, source: str, names: set[str]) -> set[Site]:
    tree = ast.parse(source, filename=rel)
    renamed: dict[str, str] = {}  # local alias -> the name it was imported as
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.asname and a.name in names:
                    renamed[a.asname] = a.name
    found: set[Site] = set()
    for scope, node, _ in _scopes(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        called = renamed.get(called, called)
        if called in names and not scope.endswith(called):  # a function is not its own caller
            found.add((rel, scope, f"call:{called}"))
    return found


def discover(sources: dict[str, str] | None = None) -> set[Site]:
    if sources is None:
        sources = {}
        for top in SCAN_DIRS:
            for path in sorted((REPO_ROOT / top).rglob("*.py")):
                rel = path.relative_to(REPO_ROOT).as_posix()
                if rel not in SKIP_FILES:
                    sources[rel] = path.read_text(encoding="utf-8")
    primitives: set[Site] = set()
    for rel, text in sources.items():
        primitives |= _primitive_sites(rel, text)
    # Writers the package exposes by bare name. A script's own helpers are
    # registered as primitives but are not callable from anywhere else.
    names = {
        fn
        for rel, fn, what in primitives
        if rel.startswith("jscc/") and "." not in fn and fn != "<module>"
    }
    callers: set[Site] = set()
    for rel, text in sources.items():
        callers |= _call_sites(rel, text, names)
    return primitives | callers


# ---- fixtures and helpers -----------------------------------------------------------

CONFIG_DIR = REPO_ROOT / "config"
TITLE = "Staff Backend Engineer"
PASTE = f"{TITLE} wanted for the platform team. " * 12
NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


class _Extractor:
    """A model client whose answer verifies against PASTE, so nothing needs confirming."""

    def __init__(self) -> None:
        self.calls = 0
        self.on_call: Callable[[], None] | None = None

    def complete(self, *, model: str, system: str, user: str) -> LLMResponse:
        self.calls += 1
        if self.on_call is not None:
            self.on_call()
        payload = {
            "title": TITLE,
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
    extractor: _Extractor

    def conn(self) -> sqlite3.Connection:
        return storage.open_for_mode(Mode.synthetic, self.data_dir)

    def cli(self, *args: str, input: str | None = None):
        return CliRunner().invoke(cli, [*args, "--data-dir", str(self.data_dir)], input=input)

    def apps(self) -> list[Application]:
        conn = self.conn()
        try:
            return storage.list_applications(conn)
        finally:
            conn.close()

    def entries(self) -> list[DLQEntry]:
        conn = self.conn()
        try:
            return storage.list_dlq_entries(conn, unresolved_only=False)
        finally:
            conn.close()

    def new_entry(self, url: str = "https://example.com/jobs/5") -> str:
        conn = self.conn()
        try:
            return storage.create_dlq_entry(
                conn,
                DLQEntry(source_url=url, failure_mode=FailureMode.blocked, error_detail="HTTP 403"),
            )
        finally:
            conn.close()


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    monkeypatch.delenv(ENV_VAR, raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    extractor = _Extractor()
    monkeypatch.setattr("jscc.extraction.default_client", lambda: extractor)
    return Env(tmp_path, extractor)


def _application(**kw) -> Application:
    return Application(title="T", company="C", stage="applied", source_raw=PASTE, **kw)


def _snapshot(conn: sqlite3.Connection) -> dict[str, list[tuple]]:
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    return {t: sorted(conn.execute(f"SELECT * FROM {t}").fetchall()) for t in tables}  # noqa: S608


def _without_stamp(app: Application) -> dict:
    return app.model_dump(exclude={"updated_at"})


# ---- checks: storage primitives -----------------------------------------------------


def check_primary_key_refuses_a_replayed_create(env: Env) -> None:
    conn = env.conn()
    try:
        app = _application()
        storage.create_application(conn, app)
        contact = Contact(application_id=app.id, name="N", role=ContactRole.recruiter)
        storage.create_contact(conn, contact)
        interaction = Interaction(
            application_id=app.id, type=InteractionType.applied, occurred_at=NOW
        )
        storage.create_interaction(conn, interaction)
        entry = DLQEntry(source_url="u", failure_mode=FailureMode.other)
        storage.create_dlq_entry(conn, entry)
        for replay in (
            lambda: storage.create_application(conn, app),
            lambda: storage.create_contact(conn, contact),
            lambda: storage.create_interaction(conn, interaction),
            lambda: storage.create_dlq_entry(conn, entry),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                replay()
        assert len(storage.list_applications(conn)) == 1
        assert len(storage.list_contacts(conn, app.id)) == 1
        assert len(storage.list_interactions(conn, app.id)) == 1
        assert len(storage.list_dlq_entries(conn, unresolved_only=False)) == 1
    finally:
        conn.close()


def check_update_application_leaves_the_same_state(env: Env) -> None:
    conn = env.conn()
    try:
        app = _application()
        storage.create_application(conn, app)
        storage.update_application(conn, app.id, fit_score=7.0, fit_rationale="r")
        once = storage.get_application(conn, app.id)
        storage.update_application(conn, app.id, fit_score=7.0, fit_rationale="r")
        again = storage.get_application(conn, app.id)
        assert once is not None and again is not None
        assert _without_stamp(once) == _without_stamp(again)
        assert once.fit_score == 7.0
        # Updating a row that is not there makes none.
        storage.update_application(conn, "no-such-id", fit_score=1.0)
        assert len(storage.list_applications(conn)) == 1
    finally:
        conn.close()


def check_delete_application_twice_is_harmless(env: Env) -> None:
    conn = env.conn()
    try:
        app = _application()
        storage.create_application(conn, app)
        storage.create_contact(
            conn, Contact(application_id=app.id, name="N", role=ContactRole.recruiter)
        )
        storage.delete_application(conn, app.id)
        storage.delete_application(conn, app.id)
        assert storage.list_applications(conn) == []
        assert storage.list_contacts(conn, app.id) == []
    finally:
        conn.close()


def check_resolve_is_a_compare_and_set_when_asked(env: Env) -> None:
    entry_id = env.new_entry()
    conn = env.conn()
    try:
        won = storage.resolve_dlq_entry(
            conn, entry_id, Resolution.manual_paste, only_if_unresolved=True
        )
        lost = storage.resolve_dlq_entry(
            conn, entry_id, Resolution.wont_fix, only_if_unresolved=True
        )
        assert (won, lost) == (True, False)
        [entry] = storage.list_dlq_entries(conn, unresolved_only=False)
        assert entry.resolution is Resolution.manual_paste  # the loser changed nothing
    finally:
        conn.close()


def check_ledger_appends_one_row_per_attempt(env: Env) -> None:
    conn = env.conn()
    try:
        record = LLMCallRecord(
            feature="f",
            model="m",
            prompt_hash="h",
            input_tokens=1,
            output_tokens=1,
            cost_usd=0.1,
            latency_ms=1.0,
        )
        storage.record_llm_call(conn, record)
        storage.record_llm_call(conn, record.model_copy(update={"id": "another"}))
        assert len(storage.list_llm_calls(conn)) == 2
    finally:
        conn.close()


def check_reopening_a_database_changes_nothing(env: Env) -> None:
    conn = env.conn()
    try:
        storage.create_application(conn, _application())
        storage.write_mode_marker(conn, Mode.synthetic)
        storage.write_mode_marker(conn, Mode.synthetic)
        before = _snapshot(conn)
        version = storage.schema_version(conn)
    finally:
        conn.close()
    for _ in range(2):
        reopened = env.conn()
        try:
            assert _snapshot(reopened) == before
            assert storage.schema_version(reopened) == version
            assert storage.read_mode_marker(reopened) is Mode.synthetic
            storage._migrate(reopened)  # repeating the column repair adds nothing
            assert _snapshot(reopened) == before
        finally:
            reopened.close()


def check_reseeding_rebuilds_the_same_fixture(env: Env) -> None:
    conn = env.conn()
    try:
        first = seed_synthetic(conn, now=NOW)
        titles = sorted(a.title for a in storage.list_applications(conn))
        marker = storage.read_mode_marker(conn)
        second = seed_synthetic(conn, now=NOW)
        assert first == second
        assert sorted(a.title for a in storage.list_applications(conn)) == titles
        assert len(storage.list_dlq_entries(conn, unresolved_only=False)) == first["dlq_entries"]
        assert storage.read_mode_marker(conn) is marker  # the stamp survives a reseed
    finally:
        conn.close()


def check_save_recording_only_adds_or_updates_keys(env: Env) -> None:
    path = env.data_dir / "recorded.json"
    evals.save_recording({"a": "1"}, path)
    evals.save_recording({"b": "2"}, path)
    evals.save_recording({"b": "2"}, path)
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": "1", "b": "2"}
    evals.save_recording({}, path)  # a run that captured nothing loses nothing
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": "1", "b": "2"}


# ---- checks: operations built on them -----------------------------------------------


def check_ingest_of_the_same_text_is_refused_or_updates_in_place(env: Env) -> None:
    args = ("ingest", "--paste", "--title", "Mine", "--company", "Acme")
    assert env.cli(*args, input=PASTE).exit_code == 0
    refused = env.cli(*args, input=PASTE)
    assert refused.exit_code == 2, refused.output  # stdin is spent, so --update is required
    assert len(env.apps()) == 1
    for _ in range(2):
        assert env.cli(*args, "--update", input=PASTE).exit_code == 0
    [only] = env.apps()
    assert (only.title, only.company, only.source_raw) == ("Mine", "Acme", PASTE)


def check_score_twice_stores_one_result(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = env.conn()
    try:
        app = _application(
            extracted_jd={
                "title": TITLE,
                "company": None,
                "level": "staff",
                "comp_band": None,
                "location": None,
                "remote_policy": None,
                "must_have_skills": [],
                "responsibilities_summary": "s",
            }
        )
        storage.create_application(conn, app)
    finally:
        conn.close()
    monkeypatch.setattr(
        "jscc.cli.agents.score_fit", lambda *a, **k: SimpleNamespace(score=7.0, rationale="r")
    )
    codes = [env.cli("score", app.id, "--config-dir", str(CONFIG_DIR)).exit_code for _ in range(2)]
    assert codes == [0, 0]
    [stored] = env.apps()
    assert (stored.id, stored.fit_score, stored.fit_rationale) == (app.id, 7.0, "r")


def check_resolving_an_entry_twice_creates_one_application(env: Env) -> None:
    entry_id = env.new_entry()
    first = env.cli("resolve-dlq", entry_id, "--paste-text", PASTE)
    assert first.exit_code == 0, first.output
    env.cli("resolve-dlq", entry_id, "--paste-text", PASTE)
    assert len(env.apps()) == 1
    assert env.extractor.calls == 1  # the replay did not spend a second model call
    # The same posting reached through a second entry is linked, not recreated.
    other = env.new_entry()
    env.cli("resolve-dlq", other, "--paste-text", PASTE)
    assert len(env.apps()) == 1
    assert env.extractor.calls == 1


def check_a_resolver_that_loses_the_race_leaves_no_second_application(env: Env) -> None:
    entry_id = env.new_entry()

    def another_process_resolves_first() -> None:
        conn = env.conn()
        try:
            storage.resolve_dlq_entry(conn, entry_id, Resolution.manual_paste)
        finally:
            conn.close()

    env.extractor.on_call = another_process_resolves_first
    conn = env.conn()
    try:
        result = dlq.resolve_dlq_entry_via_paste(conn, entry_id, PASTE)
    finally:
        conn.close()
    assert result.outcome is dlq.DLQResolveOutcome.already_resolved
    assert env.apps() == []  # the Application this call made was undone


def check_a_failed_fetch_is_queued_once_per_attempt(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "jscc.cli.ingest.fetch_jd",
        lambda url, **kw: FetchResult(ok=False, failure_mode=FailureMode.blocked, error_detail="x"),
    )
    for _ in range(2):
        env.cli("ingest", "--url", "https://example.com/jobs/9")
    # Each failed attempt queues its own entry. That is the pinned behavior; a
    # change to one entry per URL is a product decision, and this row says so.
    assert len(env.entries()) == 2


def check_pending_extraction_is_single_use_and_keyed(env: Env) -> None:
    extracted = SimpleNamespace(title=TITLE)
    dlq._store_pending_extraction("entry-1", extracted, PASTE)  # type: ignore[arg-type]
    dlq._store_pending_extraction("entry-1", extracted, PASTE)  # type: ignore[arg-type]
    assert list(dlq._PENDING_EXTRACTIONS) == ["entry-1"]  # a re-store replaces
    assert dlq._pop_pending_extraction("entry-1", PASTE) is extracted
    assert dlq._pop_pending_extraction("entry-1", PASTE) is None  # consumed
    dlq._store_pending_extraction("entry-2", extracted, PASTE)  # type: ignore[arg-type]
    assert dlq._pop_pending_extraction("entry-2", PASTE + "!") is None  # another text
    assert dlq._pop_pending_extraction("entry-2", PASTE) is None  # and not kept for later


def check_one_lock_per_entry_and_resolve_holds_it(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert dlq._entry_lock("e-1") is dlq._entry_lock("e-1")
    assert dlq._entry_lock("e-1") is not dlq._entry_lock("e-2")
    held: list[bool] = []

    def probe(*a, **k):
        held.append(dlq._entry_lock("e-1").locked())
        return "sentinel"

    monkeypatch.setattr(dlq, "_resolve_locked", probe)
    threads = [
        threading.Thread(target=dlq.resolve_dlq_entry_via_paste, args=(None, "e-1", PASTE))
        for _ in range(3)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert held == [True, True, True]


def check_instrumented_call_records_each_attempt(env: Env) -> None:
    @instrumentation.instrumented("probe")
    def good(conn, model, prompt):
        return instrumentation.LLMResult(output="ok", input_tokens=1, output_tokens=1, cost_usd=0.1)

    @instrumentation.instrumented("probe")
    def bad(conn, model, prompt):
        raise ConnectionError("reset")

    conn = env.conn()
    try:
        assert good(conn, "m", "p") == "ok"
        assert good(conn, "m", "p") == "ok"
        with pytest.raises(ConnectionError):
            bad(conn, "m", "p")
        rows = storage.list_llm_calls(conn)
        assert len(rows) == 3
        assert sorted(bool(r.error) for r in rows) == [False, False, True]
    finally:
        conn.close()


# ---- the registry -------------------------------------------------------------------

PROPERTIES = {
    "natural_key",
    "compare_and_set",
    "same_state",
    "schema_stamp",
    "merge_only",
    "reset_rebuild",
    "append_per_attempt",
    "consume_once",
    "serialized",
    "caller_keyed",
}


@dataclass(frozen=True)
class Row:
    file: str
    function: str
    what: str
    operation: str
    prop: str
    check: Callable[..., None]

    @property
    def site(self) -> Site:
        return (self.file, self.function, self.what)


def _rows(file: str, prop: str, check: Callable[..., None], *sites: tuple[str, str, str]):
    return [Row(file, fn, what, op, prop, check) for fn, what, op in sites]


S = "jscc/storage.py"
D = "jscc/dlq.py"
_OPEN = "open or create the mode database"
_SEED = "seed the fixture"

REGISTRY: list[Row] = [
    *_rows(
        S,
        "natural_key",
        check_primary_key_refuses_a_replayed_create,
        ("create_application", "sql", "insert an application"),
        ("create_contact", "sql", "insert a contact"),
        ("create_interaction", "sql", "insert an interaction"),
        ("create_dlq_entry", "sql", "queue a failed fetch"),
    ),
    *_rows(
        S,
        "same_state",
        check_update_application_leaves_the_same_state,
        ("update_application", "sql", "overwrite named fields"),
    ),
    *_rows(
        S,
        "same_state",
        check_delete_application_twice_is_harmless,
        ("delete_application", "sql", "remove an application"),
    ),
    *_rows(
        S,
        "compare_and_set",
        check_resolve_is_a_compare_and_set_when_asked,
        ("resolve_dlq_entry", "sql", "mark an entry resolved"),
    ),
    *_rows(
        S,
        "append_per_attempt",
        check_ledger_appends_one_row_per_attempt,
        ("record_llm_call", "sql", "append a ledger row"),
    ),
    *_rows(
        S,
        "schema_stamp",
        check_reopening_a_database_changes_nothing,
        ("write_mode_marker", "sql", "stamp the mode"),
        ("_ensure_meta_table", "sql", "create the meta table"),
        ("_migrate", "sql", "add a missing column"),
        ("_connect", "file:mkdir", "create the data directory"),
        ("_init_db", "call:_migrate", "create and migrate the schema"),
        ("open_for_mode", "call:_connect", _OPEN),
        ("open_for_mode", "call:_ensure_meta_table", _OPEN),
        ("open_for_mode", "call:_migrate", _OPEN),
        ("open_for_mode", "call:write_mode_marker", _OPEN),
    ),
    *_rows(
        "jscc/seed.py",
        "reset_rebuild",
        check_reseeding_rebuilds_the_same_fixture,
        ("reset_tables", "sql", "clear every table but the stamp"),
        ("seed_synthetic", "call:reset_tables", _SEED),
        ("seed_synthetic", "call:create_application", _SEED),
        ("seed_synthetic", "call:create_contact", _SEED),
        ("seed_synthetic", "call:create_interaction", _SEED),
        ("seed_synthetic", "call:create_dlq_entry", _SEED),
        ("seed_synthetic", "call:resolve_dlq_entry", _SEED),
    ),
    *_rows(
        "jscc/evals.py",
        "merge_only",
        check_save_recording_only_adds_or_updates_keys,
        ("save_recording", "file:mkdir", "merge captured responses"),
        ("save_recording", "file:write_text", "merge captured responses"),
    ),
    *_rows(
        "jscc/cli/eval_cmds.py",
        "merge_only",
        check_save_recording_only_adds_or_updates_keys,
        *[
            (fn, "call:save_recording", "record eval captures")
            for fn in (
                "eval_composition",
                "eval_extraction_to_scoring",
                "eval_fit_scoring",
                "eval_jd_extraction",
                "eval_routing",
            )
        ],
    ),
    *_rows(
        "jscc/cli/agents.py",
        "same_state",
        check_score_twice_stores_one_result,
        ("score", "call:update_application", "score an application"),
    ),
    *_rows(
        "jscc/ingest_logic.py",
        "natural_key",
        check_ingest_of_the_same_text_is_refused_or_updates_in_place,
        (
            "extract_and_create_application",
            "call:update_application",
            "re-ingest into the existing row",
        ),
    ),
    *_rows(
        "jscc/cli/ingest.py",
        "append_per_attempt",
        check_a_failed_fetch_is_queued_once_per_attempt,
        ("ingest", "call:create_dlq_entry", "queue a failed ingest"),
    ),
    *_rows(
        D,
        "serialized",
        check_one_lock_per_entry_and_resolve_holds_it,
        ("resolve_dlq_entry_via_paste", "call:_entry_lock", "resolve a queued entry"),
        ("_entry_lock", "memory:_ENTRY_LOCKS", "one lock per entry"),
    ),
    *_rows(
        D,
        "consume_once",
        check_pending_extraction_is_single_use_and_keyed,
        ("_pop_pending_extraction", "memory:_PENDING_EXTRACTIONS", "replay a held extraction"),
        ("_resolve_locked", "call:_pop_pending_extraction", "resolve a queued entry"),
    ),
    *_rows(
        D,
        "same_state",
        check_pending_extraction_is_single_use_and_keyed,
        ("_store_pending_extraction", "memory:_PENDING_EXTRACTIONS", "hold an extraction"),
        ("_resolve_locked", "call:_store_pending_extraction", "resolve a queued entry"),
    ),
    *_rows(
        D,
        "compare_and_set",
        check_resolving_an_entry_twice_creates_one_application,
        ("_resolve_locked", "call:resolve_dlq_entry", "resolve a queued entry"),
    ),
    *_rows(
        D,
        "compare_and_set",
        check_a_resolver_that_loses_the_race_leaves_no_second_application,
        ("_resolve_locked", "call:delete_application", "undo a lost race"),
    ),
    *_rows(
        "jscc/instrumentation.py",
        "append_per_attempt",
        check_instrumented_call_records_each_attempt,
        ("instrumented.decorator.wrapper", "call:record_llm_call", "ledger every model call"),
    ),
]


# ---- tests --------------------------------------------------------------------------


def _call(check: Callable[..., None], env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    names = check.__code__.co_varnames[: check.__code__.co_argcount]
    kwargs: dict[str, object] = {}
    if "env" in names:
        kwargs["env"] = env
    if "monkeypatch" in names:
        kwargs["monkeypatch"] = monkeypatch
    check(**kwargs)


@pytest.fixture(autouse=True)
def _clean_process_state():
    dlq._PENDING_EXTRACTIONS.clear()
    yield
    dlq._PENDING_EXTRACTIONS.clear()


def test_every_state_write_has_a_row() -> None:
    discovered = discover()
    registered = {r.site for r in REGISTRY}
    assert discovered - registered == set(), (
        "an operation writes state or re-runs on retry and has no row in REGISTRY; "
        "name its idempotency property and add a check that runs it twice"
    )
    assert registered - discovered == set(), "a registry row names a site that no longer exists"
    assert len(registered) == len(REGISTRY), "duplicate row"


def test_every_row_names_a_known_property() -> None:
    assert {r.prop for r in REGISTRY} <= PROPERTIES


def test_a_site_added_or_a_row_removed_is_caught() -> None:
    shapes = {
        "def f(conn):\n    conn.execute('INSERT INTO t VALUES (1)')\n": {
            ("jscc/new.py", "f", "sql")
        },
        "def f(conn):\n    conn.execute('delete from t')\n": {("jscc/new.py", "f", "sql")},
        "from pathlib import Path\ndef f():\n    Path('x').write_text('y')\n": {
            ("jscc/new.py", "f", "file:write_text")
        },
        "def f():\n    open('x', 'a')\n": {("jscc/new.py", "f", "file:open")},
        "_CACHE = {}\ndef f(k):\n    _CACHE[k] = 1\n": {("jscc/new.py", "f", "memory:_CACHE")},
        "_CACHE = {}\ndef f(k):\n    _CACHE.pop(k)\n": {("jscc/new.py", "f", "memory:_CACHE")},
    }
    for source, expected in shapes.items():
        assert expected <= discover({"jscc/new.py": source}), source
    sources = {
        p.relative_to(REPO_ROOT).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted((REPO_ROOT / "jscc").rglob("*.py"))
    }
    sources["jscc/new_writer.py"] = (
        "from .storage import create_application\n\ndef f(c, a):\n    create_application(c, a)\n"
    )
    extra = discover(sources) - {r.site for r in REGISTRY}
    assert extra == {("jscc/new_writer.py", "f", "call:create_application")}
    # Removing a row leaves its site undeclared.
    assert discover() - {r.site for r in REGISTRY[1:]} == {REGISTRY[0].site}


def _id(row: Row) -> str:
    return f"{row.file}:{row.function}:{row.what}"


@pytest.mark.parametrize("row", REGISTRY, ids=_id)
def test_each_operation_is_safe_to_repeat_as_its_row_says(
    row: Row, env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    _call(row.check, env, monkeypatch)
