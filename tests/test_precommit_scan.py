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


def test_phone_pattern_hit(tmp_path: Path) -> None:
    f = _write(tmp_path / "note.md", "call +1 415-555-0134 tomorrow\n")
    assert precommit_scan.main([str(f)]) == 1


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
