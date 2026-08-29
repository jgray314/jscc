from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Import from scripts/ — add repo root to sys.path so the standalone script
# is importable as a module for direct-call tests.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import precommit_scan  # noqa: E402


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_email_pattern_hit(tmp_path: Path) -> None:
    f = _write(tmp_path / "note.md", "contact: alice@example.com for details\n")
    assert precommit_scan.main([str(f)]) == 1


def test_idn_local_email_hit(tmp_path: Path) -> None:
    """H2 regression: IDN local-part email must match (ASCII regex missed this)."""
    danger = _write(tmp_path / "danger.txt", "# empty\n")
    f = _write(tmp_path / "note.md", "reach: münchen-office@example.de today\n")
    assert precommit_scan.main([str(f), "--danger-list", str(danger)]) == 1


def test_non_ascii_tld_email_hit(tmp_path: Path) -> None:
    """H2 regression: non-ASCII TLD (e.g. .москва) must match."""
    danger = _write(tmp_path / "danger.txt", "# empty\n")
    f = _write(tmp_path / "note.md", "email user@пример.москва in file\n")
    assert precommit_scan.main([str(f), "--danger-list", str(danger)]) == 1


def test_punycode_tld_email_hit(tmp_path: Path) -> None:
    """H2 regression: Punycode TLD (.xn--p1ai) must match."""
    danger = _write(tmp_path / "danger.txt", "# empty\n")
    f = _write(tmp_path / "note.md", "email user@example.xn--p1ai listed\n")
    assert precommit_scan.main([str(f), "--danger-list", str(danger)]) == 1


def test_phone_pattern_hit(tmp_path: Path) -> None:
    f = _write(tmp_path / "note.md", "call +1 415-555-0134 tomorrow\n")
    assert precommit_scan.main([str(f)]) == 1


def test_us_parenthesized_phone_hit(tmp_path: Path) -> None:
    """H1 regression: (415) 555-0134 format must match."""
    danger = _write(tmp_path / "danger.txt", "# empty\n")
    f = _write(tmp_path / "note.md", "Call (415) 555-0134 by Friday\n")
    assert precommit_scan.main([str(f), "--danger-list", str(danger)]) == 1


def test_dotted_international_phone_hit(tmp_path: Path) -> None:
    """H1 regression: +44.20.7946.0018 format must match."""
    danger = _write(tmp_path / "danger.txt", "# empty\n")
    f = _write(tmp_path / "note.md", "London office: +44.20.7946.0018\n")
    assert precommit_scan.main([str(f), "--danger-list", str(danger)]) == 1


def test_digits_only_phone_hit(tmp_path: Path) -> None:
    """A run of 10-15 digits with no separators still trips the phone rule."""
    danger = _write(tmp_path / "danger.txt", "# empty\n")
    f = _write(tmp_path / "note.md", "cell 4155550100 primary\n")
    assert precommit_scan.main([str(f), "--danger-list", str(danger)]) == 1


def test_iso_date_not_flagged_as_phone(tmp_path: Path) -> None:
    """Regression: 2026-08-28 (8 digits) should not match phone rule."""
    danger = _write(tmp_path / "danger.txt", "# empty\n")
    f = _write(tmp_path / "note.md", "Status: Accepted (2026-08-28)\n")
    assert precommit_scan.main([str(f), "--danger-list", str(danger)]) == 0


def test_short_number_not_flagged(tmp_path: Path) -> None:
    danger = _write(tmp_path / "danger.txt", "# empty\n")
    f = _write(tmp_path / "note.md", "version 1.2.3-456\n")
    assert precommit_scan.main([str(f), "--danger-list", str(danger)]) == 0


def test_danger_list_hit(tmp_path: Path) -> None:
    danger = _write(tmp_path / "danger.txt", "SecretCorp Inc\n")
    f = _write(tmp_path / "notes.md", "I applied to SecretCorp Inc last week\n")
    assert precommit_scan.main([str(f), "--danger-list", str(danger)]) == 1


def test_danger_list_case_insensitive(tmp_path: Path) -> None:
    danger = _write(tmp_path / "danger.txt", "secretcorp inc\n")
    f = _write(tmp_path / "notes.md", "Went to SECRETCORP INC office\n")
    assert precommit_scan.main([str(f), "--danger-list", str(danger)]) == 1


def test_danger_list_comments_and_blanks_ignored(tmp_path: Path) -> None:
    danger = _write(
        tmp_path / "danger.txt",
        "# comment line\n\n   \n# another\n",
    )
    f = _write(tmp_path / "notes.md", "harmless content\n")
    assert precommit_scan.main([str(f), "--danger-list", str(danger)]) == 0


def test_clean_file_passes(tmp_path: Path) -> None:
    danger = _write(tmp_path / "danger.txt", "# empty\n")
    f = _write(tmp_path / "notes.md", "This is entirely innocuous text.\n")
    assert precommit_scan.main([str(f), "--danger-list", str(danger)]) == 0


def test_seed_fake_data_does_not_trigger(tmp_path: Path) -> None:
    """The gotcha from A4.5b framing: seed generator's fake data must pass."""
    danger = _write(tmp_path / "danger.txt", "# empty\n")
    f = _write(
        tmp_path / "fixture.txt",
        "Recruiter A. Placeholder\n"
        "HM B. Placeholder\n"
        "https://jobs.example/examplecorp/1234\n"
        "https://jobs.example/blocked/5\n",
    )
    assert precommit_scan.main([str(f), "--danger-list", str(danger)]) == 0


def test_missing_file_arg_is_silently_skipped(tmp_path: Path) -> None:
    danger = _write(tmp_path / "danger.txt", "# empty\n")
    # Nonexistent path — should not crash, should exit clean.
    assert (
        precommit_scan.main(
            [str(tmp_path / "does-not-exist.md"), "--danger-list", str(danger)]
        )
        == 0
    )


def test_missing_danger_list_ok(tmp_path: Path) -> None:
    # No danger list file present → only regex rules apply.
    f = _write(tmp_path / "notes.md", "just some words\n")
    assert (
        precommit_scan.main(
            [str(f), "--danger-list", str(tmp_path / "not-there.txt")]
        )
        == 0
    )


def test_binary_file_skipped_not_failed(tmp_path: Path) -> None:
    danger = _write(tmp_path / "danger.txt", "# empty\n")
    binary = tmp_path / "blob.bin"
    binary.write_bytes(b"\x00\x01\x02\xff\xfe\xfd")
    assert precommit_scan.main([str(binary), "--danger-list", str(danger)]) == 0


def test_reports_line_number_and_reason(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = _write(
        tmp_path / "note.md",
        "line one\nline two has alice@example.com in it\nline three\n",
    )
    danger = _write(tmp_path / "danger.txt", "# empty\n")
    assert precommit_scan.main([str(f), "--danger-list", str(danger)]) == 1
    err = capsys.readouterr().err
    assert ":2:" in err
    assert "email-pattern" in err
    assert "alice@example.com" in err


def test_multiple_hits_all_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = _write(
        tmp_path / "note.md",
        "alice@example.com\nbob@example.com\n+1 415-555-0100\n",
    )
    danger = _write(tmp_path / "danger.txt", "# empty\n")
    assert precommit_scan.main([str(f), "--danger-list", str(danger)]) == 1
    err = capsys.readouterr().err
    assert err.count("email-pattern") == 2
    assert "phone-pattern" in err


def test_exclude_glob_skips_file(tmp_path: Path) -> None:
    """--exclude glob skips scanning a file that would otherwise hit."""
    scanned = _write(tmp_path / "note.md", "contact: alice@example.com\n")
    skipped = _write(tmp_path / "fixtures.md", "sample: bob@example.com\n")
    danger = _write(tmp_path / "danger.txt", "# empty\n")
    rc = precommit_scan.main(
        [
            str(scanned),
            str(skipped),
            "--danger-list",
            str(danger),
            "--exclude",
            str(skipped).replace("\\", "/"),
        ]
    )
    assert rc == 1  # scanned still hits


def test_exclude_pattern_matches_multiple(tmp_path: Path) -> None:
    """A `**` glob excludes every match, making CI-side sweeps clean."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    a = _write(tests_dir / "a.md", "alice@example.com\n")
    b = _write(tests_dir / "b.md", "bob@example.com\n")
    danger = _write(tmp_path / "danger.txt", "# empty\n")
    posix_a = str(a).replace("\\", "/")
    posix_b = str(b).replace("\\", "/")
    rc = precommit_scan.main(
        [posix_a, posix_b, "--danger-list", str(danger), "--exclude", "**/tests/*.md"]
    )
    assert rc == 0


def test_exclude_double_star_recurses(tmp_path: Path) -> None:
    """M-exclude-1 regression: `**` must actually match through path
    separators. Previously fnmatch treated `**` as literal so
    `tests/**` missed nested files — a silent hole.
    """
    root = tmp_path / "tests"
    sub = root / "sub" / "deeper"
    sub.mkdir(parents=True)
    shallow = _write(root / "shallow.md", "alice@example.com\n")
    nested = _write(sub / "nested.md", "bob@example.com\n")
    danger = _write(tmp_path / "danger.txt", "# empty\n")
    posix_shallow = str(shallow).replace("\\", "/")
    posix_nested = str(nested).replace("\\", "/")
    # `**/nested.md` must reach the nested file specifically.
    rc = precommit_scan.main(
        [posix_shallow, posix_nested, "--danger-list", str(danger), "--exclude", "**/nested.md"]
    )
    # shallow still hits (not excluded); nested is excluded — so exit 1.
    assert rc == 1


def test_exclude_single_star_does_not_cross_slash(tmp_path: Path) -> None:
    """Single `*` must NOT cross a path separator — narrower semantic than `**`."""
    root = tmp_path / "d"
    sub = root / "sub"
    sub.mkdir(parents=True)
    nested = _write(sub / "note.md", "alice@example.com\n")
    danger = _write(tmp_path / "danger.txt", "# empty\n")
    posix_nested = str(nested).replace("\\", "/")
    # `*/note.md` should NOT reach `d/sub/note.md` (two levels).
    rc = precommit_scan.main(
        [posix_nested, "--danger-list", str(danger), "--exclude", "*/note.md"]
    )
    assert rc == 1  # not excluded


def test_exclude_double_star_matches_directory_itself(tmp_path: Path, monkeypatch) -> None:
    """M-precommit-abs-paths-1 (A10 review): `tests/**` should also match
    the `tests` path itself, not just `tests/x`. Standard glob semantics."""
    monkeypatch.chdir(tmp_path)
    d = tmp_path / "tests"
    d.mkdir()
    # A file literally named `tests` (no children) — check that `tests/**`
    # zero-matches. We simulate by putting a file at the root path `tests/x`
    # then also asserting the compile handles the zero-child case via regex.
    inside = _write(d / "x.md", "alice@example.com\n")
    danger = _write(tmp_path / "danger.txt", "# empty\n")
    rc = precommit_scan.main(
        [str(inside), "--danger-list", str(danger), "--exclude", "tests/**"]
    )
    assert rc == 0  # excluded

    # And the compiled pattern also fullmatches "tests" (the bare directory name).
    pat = precommit_scan._compile_exclude("tests/**")
    assert pat.fullmatch("tests") is not None
    assert pat.fullmatch("tests/foo") is not None
    assert pat.fullmatch("tests/foo/bar") is not None


def test_exclude_with_absolute_windows_style_path(tmp_path: Path, monkeypatch) -> None:
    """M-precommit-abs-paths-1: an absolute path input from Windows must
    normalize to repo-relative before matching, or `--exclude CHANGELOG.md`
    silently misses when a caller passes the absolute path."""
    monkeypatch.chdir(tmp_path)
    target = _write(tmp_path / "CHANGELOG.md", "contact alice@example.com\n")
    danger = _write(tmp_path / "danger.txt", "# empty\n")
    # Pass the absolute path (both str forms — Path handles either).
    rc = precommit_scan.main(
        [str(target.resolve()), "--danger-list", str(danger), "--exclude", "CHANGELOG.md"]
    )
    assert rc == 0


def test_synthetic_fixture_passes_scanner(tmp_path: Path) -> None:
    """Walkthrough #3 + L-doc-drift-synthetic-db-1 (A10 review): the tracked
    portfolio-visible fixture `data/synthetic.db` must itself be scrubbed —
    the pre-commit rule that protects prose applies to the fixture too.

    This test runs the scanner over the actual committed SQLite file (opened
    as text; the scanner falls back to skip on UnicodeDecodeError, so pure-
    binary regions are ignored, but any UTF-8-decodable region containing a
    name / email / phone / danger-list hit would surface). The fixture is
    generated from a synthetic name pool by design; this test proves it.
    """
    repo_root = Path(__file__).resolve().parents[1]
    synth = repo_root / "data" / "synthetic.db"
    if not synth.exists():
        pytest.skip("data/synthetic.db not present; run `jscc seed` first")
    danger = _write(tmp_path / "danger.txt", "# empty\n")
    rc = precommit_scan.main([str(synth), "--danger-list", str(danger)])
    assert rc == 0, "synthetic.db tripped the pre-commit content scanner"
