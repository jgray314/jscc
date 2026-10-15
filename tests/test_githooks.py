"""The hooks and the feature-list check are exercised, so one that stops
working fails the suite instead of failing silently.

Found by the harness audit (2026-10-15): the commit-msg trailer rule, its
Proof-line rule and the feature-list tamper check had never been shown to
reject anything from inside the repo. A guard nobody tests is a guard that
may not fire.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".githooks" / "commit-msg"
FEATURES_PY = ROOT / "scripts" / "features.py"
SH = shutil.which("sh")

needs_sh = pytest.mark.skipif(SH is None, reason="no POSIX sh on PATH")


def run_commit_msg(tmp_path: Path, message: str) -> subprocess.CompletedProcess:
    msg = tmp_path / "MSG"
    msg.write_text(message, encoding="utf-8")
    return subprocess.run([SH, str(HOOK), str(msg)], capture_output=True, text=True)


@needs_sh
def test_a_plain_message_is_accepted(tmp_path: Path) -> None:
    assert run_commit_msg(tmp_path, "Add a thing\n\nWhy it matters.\n").returncode == 0


@needs_sh
def test_a_co_author_trailer_is_rejected(tmp_path: Path) -> None:
    result = run_commit_msg(
        tmp_path, "Add a thing\n\nCo-Authored-By: Someone <a" + "@" + "b.example>\n"
    )
    assert result.returncode != 0
    assert "Co-Authored-By" in result.stderr


@needs_sh
def test_a_fix_for_a_critical_finding_without_proof_is_rejected(tmp_path: Path) -> None:
    result = run_commit_msg(tmp_path, "Fix the thing\n\nCloses a Critical finding.\n")
    assert result.returncode != 0
    assert "Proof:" in result.stderr


@needs_sh
def test_a_fix_for_a_high_finding_with_proof_is_accepted(tmp_path: Path) -> None:
    message = "Fix the thing\n\nCloses a High finding.\n\nProof: mutation, reverted the guard, test failed\n"
    assert run_commit_msg(tmp_path, message).returncode == 0


@needs_sh
def test_a_non_fix_commit_that_mentions_a_finding_is_not_asked_for_proof(tmp_path: Path) -> None:
    message = "Document the High finding\n\nNo code changed.\n"
    assert run_commit_msg(tmp_path, message).returncode == 0


def make_repo(tmp_path: Path, features: dict) -> Path:
    """A throwaway repo holding a copy of the runner and the given feature list."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(FEATURES_PY, repo / "scripts" / "features.py")
    (repo / "FEATURES.json").write_text(json.dumps(features), encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    return repo


def run_check(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-I", "scripts/features.py", "check"],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def feature(fid: str, state: str, **extra) -> dict:
    return {"id": fid, "behavior": "b", "verify": "true", "state": state, **extra}


def test_a_clean_feature_list_passes_the_check(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"features": [feature("a", "not_started"), feature("b", "active")]})
    assert run_check(repo).returncode == 0


def test_a_hand_edited_passing_state_fails_the_check(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"features": [feature("a", "passing")]})
    result = run_check(repo)
    assert result.returncode != 0
    assert "without evidence" in result.stdout


def test_forged_evidence_fails_the_check(tmp_path: Path) -> None:
    forged = {
        "commit": "0" * 40,
        "at": "2026-01-01T00:00:00Z",
        "exit_code": 0,
        "seal": "not-a-real-seal",
    }
    repo = make_repo(tmp_path, {"features": [feature("a", "passing", evidence=forged)]})
    assert run_check(repo).returncode != 0


def test_two_active_features_fail_the_check(tmp_path: Path) -> None:
    repo = make_repo(tmp_path, {"features": [feature("a", "active"), feature("b", "active")]})
    result = run_check(repo)
    assert result.returncode != 0
    assert "WIP limit" in result.stdout


def test_the_pre_push_hook_and_ci_both_run_the_feature_check() -> None:
    pre_push = (ROOT / ".githooks" / "pre-push").read_text(encoding="utf-8")
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "scripts/features.py check" in pre_push
    assert "scripts/features.py check" in ci


def test_the_pre_push_hook_runs_every_ci_gate() -> None:
    """A gate CI runs and the hook skips is a red CI run found after the push.

    mypy was missing from the hook, so a Windows-only attribute passed locally
    and failed on CI's Linux runner.
    """
    pre_push = (ROOT / ".githooks" / "pre-push").read_text(encoding="utf-8")
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    for gate in ("ruff check", "ruff format --check", "mypy", "pytest"):
        assert f"uv run {gate}" in ci, f"CI no longer runs {gate}"
        assert f"uv run {gate}" in pre_push, f"pre-push skips {gate}, which CI runs"


def test_mypy_checks_one_platform_wherever_it_runs() -> None:
    """Platform-gated code (ctypes.windll) is checked as it is on CI, not per OS."""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'platform = "linux"' in pyproject
