"""The scanner is tested against its own history: what it must catch, what it must
leave alone, and that its failure reaches the shell.

The scanner's phone rule is deliberately loose, so every few weeks a legitimate
string landed in the tree that looked like a phone number: a dotted-quad address in
a mocked resolver, a model id carrying two version numbers and a date, a content
hash, a recording key. Each was patched where it appeared (a literal split in two
pieces), four times, and one pass was green locally and red in CI because the shell
wrapper swallowed the scanner's exit code under Git Bash. Patching the call site
leaves the next one to be found by CI.

This file is the scanner's own contract. The literals are assembled from pieces at
run time so the file does not contain the strings it describes and passes the scan
it tests. Describe a shape in prose; never requote one.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import precommit_scan  # noqa: E402  (script directory is not a package)

# ---- shapes, assembled from pieces ---------------------------------------------------

_AT = "@"
_HEX = "0123" + "456789abcdef"
# A 64-character hash whose middle holds a run of thirteen digits.
_HASH = "ab12cd" + "123456" + "7890123" + (_HEX * 4)[: 64 - 6 - 13]

MUST_FLAG: dict[str, tuple[str, str]] = {
    "an email address": ("reach me at jane.doe" + _AT + "example.org today", "email-pattern"),
    "an email with a non-ASCII local part": ("j\u00fcrgen" + _AT + "example.org", "email-pattern"),
    "a US phone number with dashes": ("call 415" + "-555-" + "0100 after noon", "phone-pattern"),
    "an international number with a plus": ("+44 20 " + "7946 0958", "phone-pattern"),
    "a dotted international number": ("+1." + "415.555." + "0100", "phone-pattern"),
    "a phone-shaped run directly after a claude- prefix": (
        "claude-" + "415" + "-555-" + "0100",
        "phone-pattern",
    ),
    "a bare digit run directly after a claude- prefix": (
        "claude-" + "4155550" + "100",
        "phone-pattern",
    ),
    "a phone-shaped run after a family name": (
        "claude-sonnet-" + "415" + "-555-" + "0100",
        "phone-pattern",
    ),
    "an Anthropic key shape": ("key = sk-" + "ant-" + "api03-" + "A" * 24, "credential-pattern"),
}

MUST_NOT_FLAG: dict[str, str] = {
    "an ISO date": "released 2026-09-28",
    "an ISO timestamp with an offset": "at 2026-09-28T10:59:20-07:00 sharp",
    "a short number": "ticket 4821",
    "a dotted version number": "requires 3.12.4.1 or newer",
    "a commit sha": "built from c76cfee5bccfa5068b396b9409439cac011712f3",
    "a model id with one version number and a date": "claude-sonnet-4-" + "20250514",
    "a model id with two version numbers and a date": 'model = "claude-haiku-4-5-' + '20251001"',
    "a model id with a family, then a date": "claude-opus-4-1-" + "20250805",
    "a model id with the version first": "claude-3-5-sonnet-" + "20241022",
    "a model id with only a family and a version": "claude-sonnet-5-5",
    "a model id ending in latest": "claude-3-7-sonnet-latest",
    "a public dotted-quad address": "resolved to 93.184." + "216.34",
    "a private dotted-quad address": "bound to 192.168." + "100.200",
    "a UUID whose last group is mostly digits": "id 123e4567-e89b-12d3-a456-" + "4266" + "14174000",
    "a bare content hash holding a long digit run": "digest " + _HASH,
    "a recording key with the sha256 prefix": '"sha256:' + _HASH + '": {"text": "ok"}',
    "a lockfile hash entry": 'hash = "sha256:' + _HASH + '"',
}


def _scan(line: str, terms: list[str] | None = None) -> list[tuple[str, str]]:
    return precommit_scan.scan_line(line, terms or [])


@pytest.mark.parametrize("shape", sorted(MUST_FLAG))
def test_a_known_true_positive_is_flagged_with_its_reason(shape: str) -> None:
    text, reason = MUST_FLAG[shape]
    assert reason in {r for r, _ in _scan(text)}, shape


def test_a_danger_list_term_is_flagged_case_insensitively() -> None:
    hits = _scan("Met with SOMEBODY-NAMED-ZZ yesterday", ["somebody-named-zz"])
    assert [r for r, _ in hits] == ["danger-list"]


@pytest.mark.parametrize("shape", sorted(MUST_NOT_FLAG))
def test_a_known_false_positive_shape_is_not_flagged(shape: str) -> None:
    assert _scan(MUST_NOT_FLAG[shape]) == [], shape


def test_a_recording_key_does_not_exempt_the_value_beside_it() -> None:
    line = '"sha256:' + _HASH + '": {"text": "write to a.b' + _AT + 'example.org"}'
    assert [r for r, _ in _scan(line)] == ["email-pattern"]


def test_a_false_positive_shape_does_not_hide_a_real_number_on_the_same_line() -> None:
    line = "host 93.184." + "216.34 " + "call 415" + "-555-" + "0100"
    assert [r for r, _ in _scan(line)] == ["phone-pattern"]


def test_a_danger_term_inside_a_carved_out_shape_is_still_flagged() -> None:
    line = "claude-haiku-4-5-" + "20251001-somebody-named-zz"
    assert "danger-list" in {r for r, _ in _scan(line, ["somebody-named-zz"])}


def test_every_recording_in_the_repo_scans_clean_without_an_exclusion() -> None:
    recordings = sorted((ROOT / "evals").glob("*/recorded.json"))
    assert recordings, "no recordings found; the glob is stale"
    dirty = {
        p.relative_to(ROOT).as_posix(): precommit_scan.scan_file(p, [])[:1] for p in recordings
    }
    assert {k: v for k, v in dirty.items() if v} == {}


def test_this_file_passes_the_scan_it_tests() -> None:
    assert precommit_scan.scan_file(Path(__file__), []) == []


# ---- the shell wrapper ---------------------------------------------------------------


def _bashes() -> list[str]:
    found = []
    for candidate in (
        shutil.which("bash"),
        r"C:\Program Files\Git\bin\bash.exe",
        "/bin/bash",
        "/usr/bin/bash",
    ):
        if not candidate or not Path(candidate).exists():
            continue
        # WSL's launcher is not the shell the project's scripts run under.
        if "system32" in candidate.lower():
            continue
        if candidate not in found:
            found.append(candidate)
    return found


@pytest.fixture
def tracked_repo(tmp_path: Path) -> Path:
    """A throwaway git repo holding the real wrapper and scanner, with one tracked file."""
    (tmp_path / "scripts").mkdir()
    for name in ("scan_tracked.sh", "precommit_scan.py"):
        shutil.copy(ROOT / "scripts" / name, tmp_path / "scripts" / name)
    shutil.copytree(ROOT / "jscc", tmp_path / "jscc", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / ".safety", tmp_path / ".safety")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    return tmp_path


def _run_wrapper(bash: str, repo: Path, *files: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    return subprocess.run(
        [bash, "scripts/scan_tracked.sh", *files],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _track(repo: Path, name: str, text: str) -> None:
    (repo / name).write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", name], cwd=repo, check=True)


@pytest.mark.parametrize("bash", _bashes() or [pytest.param("", marks=pytest.mark.skip("no bash"))])
def test_the_wrapper_exits_nonzero_when_the_scan_finds_something(
    bash: str, tracked_repo: Path
) -> None:
    _track(tracked_repo, "notes.txt", "mail jane.doe" + _AT + "example.org\n")

    result = _run_wrapper(bash, tracked_repo)

    assert result.returncode != 0, result.stderr
    assert "email-pattern" in result.stderr


@pytest.mark.parametrize("bash", _bashes() or [pytest.param("", marks=pytest.mark.skip("no bash"))])
def test_the_wrapper_exits_nonzero_in_the_files_given_mode_too(
    bash: str, tracked_repo: Path
) -> None:
    _track(tracked_repo, "notes.txt", "mail jane.doe" + _AT + "example.org\n")

    result = _run_wrapper(bash, tracked_repo, "notes.txt")

    assert result.returncode != 0, result.stderr


@pytest.mark.parametrize("bash", _bashes() or [pytest.param("", marks=pytest.mark.skip("no bash"))])
def test_the_wrapper_exits_zero_on_a_clean_tree(bash: str, tracked_repo: Path) -> None:
    _track(tracked_repo, "notes.txt", "nothing personal here\n")

    result = _run_wrapper(bash, tracked_repo)

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("bash", _bashes() or [pytest.param("", marks=pytest.mark.skip("no bash"))])
def test_the_wrapper_skips_excluded_files_but_still_fails_on_others(
    bash: str, tracked_repo: Path
) -> None:
    bad = "mail jane.doe" + _AT + "example.org\n"
    _track(tracked_repo, "CHANGELOG.md", bad)
    assert _run_wrapper(bash, tracked_repo).returncode == 0

    _track(tracked_repo, "README.md", bad)
    assert _run_wrapper(bash, tracked_repo).returncode != 0


# ---- the lockfile ---------------------------------------------------------------------


def test_the_lockfile_scans_clean_without_an_exclusion() -> None:
    assert precommit_scan.scan_file(ROOT / "uv.lock", []) == []


def test_the_wrapper_no_longer_excludes_the_lockfile() -> None:
    """Its hash runs used to need an exclusion; the scanner now knows the shape, so
    the lockfile's values are scanned like everything else."""
    script = (ROOT / "scripts" / "scan_tracked.sh").read_text(encoding="utf-8")
    exclude_lines = [ln for ln in script.splitlines() if "--exclude" in ln]
    assert exclude_lines, "the wrapper lists no excludes; the parse is wrong"
    assert not any("uv.lock" in ln for ln in exclude_lines)


@pytest.mark.parametrize("bash", _bashes() or [pytest.param("", marks=pytest.mark.skip("no bash"))])
def test_a_leaked_address_in_a_lockfile_is_caught_by_the_wrapper(
    bash: str, tracked_repo: Path
) -> None:
    _track(tracked_repo, "uv.lock", 'name = "x"\n# mail jane.doe' + _AT + "example.org\n")

    assert _run_wrapper(bash, tracked_repo).returncode != 0
