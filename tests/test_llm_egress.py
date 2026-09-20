"""Every model call goes through the sanitizer and the send boundary, enforced by test.

`send_to_llm` verifies an authenticated payload, but the model client's `complete`
takes three plain strings, so nothing at the type level stops a new caller from
assembling them itself and skipping the sanitizer. ADR-005's addendum deferred a
type-level fix until a second module called `complete`. Four do now, so this file
holds the line by inspection instead: it fails when a caller appears that this test
does not know about, when a known caller stops routing through the boundary, or when
a stage would still reach the client after the boundary refuses.

Three checks, each of which fails for a different mistake:

- the set of modules that call `.complete(` is exactly the known four;
- inside each, the entry function sanitizes, then verifies, and hands the client only
  values read out of the verified payload;
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

# The modules allowed to call the model client, each with the entry function that owns
# the sanitize -> verify -> send sequence. Adding a caller means adding it here on
# purpose, in the same change that routes it through the boundary.
STAGES = {
    "extraction": "extract_jd",
    "scoring": "score_fit",
    "routing": "route_followup",
    "composition": "compose_followup",
}

# Modules that mention `.complete(` without being a stage: `llm_client` defines the
# clients, and `evals` wraps a client to record or replay an already-verified call.
NOT_STAGES = {"llm_client.py", "evals.py"}


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


def test_only_the_known_modules_call_the_model_client() -> None:
    callers = set()
    for path in PACKAGE.glob("*.py"):
        if path.name in NOT_STAGES:
            continue
        if any(_is_complete_call(n) for n in ast.walk(_parse(path))):
            callers.add(path.stem)
    assert callers == set(STAGES), (
        f"modules calling the model client changed: {sorted(callers)} vs {sorted(STAGES)}. "
        "A new caller must go through sanitize_for_llm and send_to_llm; add it to STAGES "
        "in this test in the same change, and revisit ADR-005's addendum."
    )


@pytest.mark.parametrize("module, entry", sorted(STAGES.items()))
def test_entry_function_sanitizes_then_verifies_then_calls(module: str, entry: str) -> None:
    fn = _functions(_module_tree(module))[entry]
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]

    def first_line(name: str) -> int:
        lines = [c.lineno for c in calls if _call_name(c) == name]
        assert lines, f"{module}.{entry} never calls {name}"
        return min(lines)

    sanitize, verify = first_line("sanitize_for_llm"), first_line("send_to_llm")
    assert sanitize < verify, f"{module}.{entry} verifies before it sanitizes"

    reaching_client = [c for c in calls if _is_complete_call(c) or _call_name(c) == "call"]
    assert reaching_client, f"{module}.{entry} has no path to the client"
    for call in reaching_client:
        assert call.lineno > verify, f"{module}.{entry} reaches the client before verifying"
        values = [*call.args, *(kw.value for kw in call.keywords)]
        for value in values:
            from_verified = (
                isinstance(value, ast.Subscript)
                and isinstance(value.value, ast.Name)
                and value.value.id == "verified"
            )
            plumbing = isinstance(value, ast.Name) and value.id in {"conn", "client"}
            assert from_verified or plumbing, (
                f"{module}.{entry} passes {ast.unparse(value)!r} to the client; "
                "only values read from the verified payload may go out"
            )


@pytest.mark.parametrize("module", sorted(STAGES))
def test_no_other_function_in_a_stage_calls_the_client_except_the_raw_helper(module: str) -> None:
    """The `_raw_*` helper is the billed unit and is handed verified strings by the entry."""
    tree = _module_tree(module)
    for name, fn in _functions(tree).items():
        if name == STAGES[module] or name.startswith("_raw_"):
            continue
        assert not any(_is_complete_call(n) for n in ast.walk(fn)), (
            f"{module}.{name} calls the model client outside the sanitized entry path"
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

    monkeypatch.setattr(importlib.import_module(f"jscc.{module}"), "send_to_llm", refuse)
    client = _ExplodingClient()
    with pytest.raises(LLMSendError):
        _run(module, client)
    assert client.calls == 0
