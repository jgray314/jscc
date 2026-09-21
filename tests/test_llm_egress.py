"""Every model call goes through the sanitizer and the send boundary, enforced by test.

`send_to_llm` verifies an authenticated payload, but the model client's `complete`
takes three plain strings, so nothing at the type level stops new code from calling it
directly and skipping the sanitizer. The four LLM stages share one call path,
`stage_call.call_stage`, and this file holds the line around it:

- every `.py` file under `jscc/`, subpackages included, is scanned, and the only module
  that calls `.complete(` is `stage_call`;
- inside `stage_call`, `call_stage` sanitizes, then verifies, and hands the client only
  values derived from the verified payload, and only `call_stage` and the billed
  `_raw_call` touch the client;
- each stage's entry function goes through `call_stage`;
- with the send boundary made to refuse, no stage reaches its client.
"""

from __future__ import annotations

import ast
import importlib
from collections.abc import Callable
from pathlib import Path

import pytest

from jscc.config import Profile
from jscc.models import Application, ExtractedJD, Interaction
from jscc.sanitizer import LLMSendError

PACKAGE = Path(__file__).resolve().parents[1] / "jscc"

# Each LLM stage and the entry function that owns its call. Adding a stage means adding
# it here on purpose, in the same change that routes it through `call_stage`.
STAGES = {
    "extraction": "extract_jd",
    "scoring": "score_fit",
    "routing": "route_followup",
    "composition": "compose_followup",
}

# The one module allowed to call the model client.
CHOKE_POINT = "stage_call.py"

# Files that mention `.complete(` without sending anything: `llm_client` defines the
# clients, and `evals` wraps a client to record or replay a call `call_stage` already made.
NOT_SENDERS = {"llm_client.py", "evals.py"}


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _is_complete_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "complete"
    )


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    return None


def _functions(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    return {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}


def _module_tree(module: str) -> ast.Module:
    return _parse(PACKAGE / f"{module}.py")


def model_callers(root: Path) -> tuple[list[Path], set[str]]:
    """Every `.py` file under `root`, recursively, and the ones that call `.complete(`."""
    scanned = sorted(root.rglob("*.py"))
    callers = {
        path.relative_to(root).as_posix()
        for path in scanned
        if path.name not in NOT_SENDERS
        and any(_is_complete_call(n) for n in ast.walk(_parse(path)))
    }
    return scanned, callers


def test_the_scan_covers_subpackages() -> None:
    scanned, _ = model_callers(PACKAGE)
    relative = {p.relative_to(PACKAGE).as_posix() for p in scanned}
    assert any(r.startswith("cli/") for r in relative), (
        "the egress scan no longer reaches jscc/cli/; it must walk subpackages"
    )


def test_the_scan_catches_a_caller_in_a_subpackage(tmp_path: Path) -> None:
    nested = tmp_path / "sub"
    nested.mkdir()
    (nested / "leak.py").write_text(
        "def go(client, text):\n    return client.complete(model='m', system='s', user=text)\n",
        encoding="utf-8",
    )
    _, callers = model_callers(tmp_path)
    assert callers == {"sub/leak.py"}


def test_only_the_choke_point_calls_the_model_client() -> None:
    _, callers = model_callers(PACKAGE)
    assert callers == {CHOKE_POINT}, (
        f"modules calling the model client: {sorted(callers)}. Model calls go through "
        "stage_call.call_stage, which sanitizes and verifies first; revisit ADR-005 "
        "before adding another."
    )


def test_call_stage_sanitizes_then_verifies_then_calls() -> None:
    fn = _functions(_module_tree("stage_call"))["call_stage"]
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]

    def first_line(name: str) -> int:
        lines = [c.lineno for c in calls if _call_name(c) == name]
        assert lines, f"call_stage never calls {name}"
        return min(lines)

    sanitize, verify = first_line("sanitize_for_llm"), first_line("send_to_llm")
    assert sanitize <= verify, "call_stage verifies before it sanitizes"

    # `prompt` is the one local allowed out besides `verified[...]`: it must be the
    # serialized verified user payload and nothing else.
    prompt_sources = [
        n.value
        for n in ast.walk(fn)
        if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "prompt" for t in n.targets)
    ]
    assert [ast.unparse(v) for v in prompt_sources] == ["serialize_user(verified['user'])"]

    reaching_client = [
        c
        for c in calls
        if _is_complete_call(c)
        or (isinstance(c.func, ast.Call) and _call_name(c.func) == "_instrumented_call")
    ]
    assert len(reaching_client) == 2, "expected one recorded and one unrecorded call path"
    for call in reaching_client:
        assert call.lineno > verify, "call_stage reaches the client before verifying"
        for value in [*call.args, *(kw.value for kw in call.keywords)]:
            from_verified = (
                isinstance(value, ast.Subscript)
                and isinstance(value.value, ast.Name)
                and value.value.id == "verified"
            )
            plumbing = isinstance(value, ast.Name) and value.id in {"conn", "client", "prompt"}
            assert from_verified or plumbing, (
                f"call_stage passes {ast.unparse(value)!r} to the client; "
                "only values derived from the verified payload may go out"
            )


def test_only_call_stage_and_the_billed_unit_touch_the_client() -> None:
    for name, fn in _functions(_module_tree("stage_call")).items():
        if name in {"call_stage", "_raw_call"}:
            continue
        assert not any(_is_complete_call(n) for n in ast.walk(fn)), (
            f"stage_call.{name} calls the model client outside call_stage"
        )


@pytest.mark.parametrize("module, entry", sorted(STAGES.items()))
def test_each_stage_goes_through_call_stage(module: str, entry: str) -> None:
    fn = _functions(_module_tree(module))[entry]
    assert any(isinstance(n, ast.Call) and _call_name(n) == "call_stage" for n in ast.walk(fn)), (
        f"{module}.{entry} does not call call_stage"
    )


# ---- behaviour: a refused payload never reaches the client ----------------------


class _ExplodingClient:
    """Fails loudly if any stage reaches it."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *, model: str, system: str, user: str):
        self.calls += 1
        raise AssertionError("the client was reached after the send boundary refused")


def _extracted() -> ExtractedJD:
    return ExtractedJD(
        title="Engineering Manager",
        level="senior",
        comp_band=None,
        location=None,
        remote_policy=None,
        must_have_skills=[],
        responsibilities_summary="Leads a team.",
    )


def _profile() -> Profile:
    return Profile(
        display_name="Sample Candidate",
        role_focus=["engineering manager"],
        level_target="L6-L7 equivalent",
        experience_years=10,
        comp_target={"min_usd": 300000, "max_usd": 500000},
        must_haves=[],
        deal_breakers=[],
    )


def _app() -> Application:
    return Application(title="Engineering Manager", company="Acme Corp", stage="screen")


def _history() -> list[Interaction]:
    return [
        Interaction(
            application_id="app-1",
            type="screen",
            occurred_at="2026-09-01T10:00:00Z",
            notes="Recruiter screen.",
        )
    ]


def _run(module: str, client: _ExplodingClient) -> None:
    mod = importlib.import_module(f"jscc.{module}")
    stage: Callable[[], object] = {
        "extraction": lambda: mod.extract_jd("A posting.", client=client),
        "scoring": lambda: mod.score_fit(_extracted(), "A posting.", _profile(), client=client),
        "routing": lambda: mod.route_followup(_app(), _history(), client=client),
        "composition": lambda: mod.compose_followup(
            _app(), _history(), "post_interview_thank_you", [], client=client
        ),
    }[module]
    stage()


@pytest.mark.parametrize("module", sorted(STAGES))
def test_a_refused_payload_never_reaches_the_client(
    module: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(payload):
        raise LLMSendError("refused for this test")

    monkeypatch.setattr(importlib.import_module("jscc.stage_call"), "send_to_llm", refuse)
    client = _ExplodingClient()
    with pytest.raises(LLMSendError):
        _run(module, client)
    assert client.calls == 0
