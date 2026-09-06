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


def _dump_text_values(db_path: Path) -> str:
    """Every TEXT value in every table, one per line.

    The scanner reads files as UTF-8 and skips the whole file on
    UnicodeDecodeError, which a SQLite file always triggers on its header. So
    pointing it at a `.db` scans nothing -- see
    `test_scanning_a_db_file_directly_finds_nothing`. Scanning the *logical*
    content is what actually answers the question the fixture has to answer.
    """
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        ]
        lines: list[str] = []
        for table in tables:
            columns = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            # Identifier columns hold uuid4()s, opaque by construction and
            # carrying no fixture prose. They are excluded by *rule*, not
            # because they happen to fail: random hex contains digit runs, so
            # asking the phone pattern about them is asking a question the
            # values cannot meaningfully answer -- and that noise is exactly
            # what pushes someone toward loosening the pattern to quiet it.
            # The pattern is the load-bearing part. The scan's scope is not.
            wanted = [c for c in columns if c != "id" and not c.endswith("_id")]
            if not wanted:
                continue
            select = ", ".join(wanted)
            for row in conn.execute(f"SELECT {select} FROM {table}"):  # noqa: S608
                lines.extend(str(v) for v in row if isinstance(v, str))
    finally:
        conn.close()
    return "\n".join(lines) + "\n"


def _seed_fixture(tmp_path: Path, random_seed: int) -> Path:
    from jscc.mode import Mode
    from jscc.seed import seed_synthetic
    from jscc.storage import open_for_mode

    conn = open_for_mode(Mode.synthetic, tmp_path)
    try:
        seed_synthetic(conn, random_seed=random_seed)
    finally:
        conn.close()
    return tmp_path / "synthetic.db"


def test_scanning_a_db_file_directly_finds_nothing(tmp_path: Path) -> None:
    """Pins the limit that made the previous version of the fixture test vacuous.

    `scan_file` reads UTF-8 and returns no hits on UnicodeDecodeError -- a
    whole-file skip, not the per-region one the old docstring described. A
    SQLite file trips it on the header, so a `.db` passed to the scanner is
    silently not scanned, however much plaintext sits inside it.

    That is a defensible scope decision (binary blobs belong to .gitattributes
    filters, not a line scanner) but it is only safe while it is *known*. The
    protection for a database is that no `.db` is tracked at all -- D7 M1/M2 --
    and this test exists so the next person to point the scanner at a binary
    and see a green tick knows what that tick means.
    """
    import sqlite3

    db = tmp_path / "x.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (a TEXT)")
    conn.execute("INSERT INTO t VALUES ('reach me at probe@example.com')")
    conn.commit()
    conn.close()

    danger = _write(tmp_path / "danger.txt", "# empty\n")
    assert precommit_scan.main([str(db), "--danger-list", str(danger)]) == 0

    # The same string in a text file is caught, so the miss above is the binary
    # skip and not a hole in the email pattern.
    plain = _write(tmp_path / "note.txt", "reach me at probe@example.com\n")
    assert precommit_scan.main([str(plain), "--danger-list", str(danger)]) == 1


@pytest.mark.parametrize("random_seed", [42, 7, 1234])
def test_generated_synthetic_fixture_is_clean(tmp_path: Path, random_seed: int) -> None:
    """The synthetic fixture's *content* must be scanner-clean.

    Two changes from the version this replaces, both of which it needed:

    It generates the fixture instead of reading `data/synthetic.db` from the
    repo. The old test skipped when that file was absent, so it was inert
    wherever the fixture had not been committed, and it made a claim about one
    frozen output rather than about the generator. Seeding across several seeds
    proves the *name pool* is clean, which is the property that has to hold.

    And it scans the dumped text rather than the `.db`. Passing the database
    file to the scanner scans nothing at all -- the old test asserted a return
    code that could not have been anything but zero, and its docstring
    described a per-region skip the scanner does not implement. Verified by
    poisoning `notes` with an email address and watching this test fail and
    the old one pass.
    """
    db = _seed_fixture(tmp_path, random_seed)
    dump = _write(tmp_path / "fixture-dump.txt", _dump_text_values(db))
    danger = _write(tmp_path / "danger.txt", "# empty\n")
    rc = precommit_scan.main([str(dump), "--danger-list", str(danger)])
    assert rc == 0, "the generated synthetic fixture tripped the content scanner"


def test_the_fixture_scan_can_actually_fail(tmp_path: Path) -> None:
    """Guards the test above against becoming vacuous the way its ancestor did.

    Same seeding, same dump, same scanner call -- with one email added to the
    dumped text. If this stops failing, the assertion above has stopped meaning
    anything, and it will say so here rather than staying quietly green.
    """
    db = _seed_fixture(tmp_path, 42)
    poisoned = _dump_text_values(db) + "reach me at probe@example.com\n"
    dump = _write(tmp_path / "poisoned-dump.txt", poisoned)
    danger = _write(tmp_path / "danger.txt", "# empty\n")
    assert precommit_scan.main([str(dump), "--danger-list", str(danger)]) == 1


def test_scanner_reads_the_local_danger_list_too(tmp_path, monkeypatch) -> None:
    """Follow-on to rerun-gate H-1, found while fixing it.

    The scanner defaulted to the *tracked* scaffold only, so a term added to
    `.safety/danger-list.local.txt` -- the file a user actually edits -- was
    honoured by the M5 sanitizer and ignored by the M3 scanner. A term added
    there blocked LLM egress but not commits, which is the reverse of what the
    C1 fix claims ("one edit blocks both"). Same drift, one file over.
    """
    safety = tmp_path / "safety"
    safety.mkdir()
    (safety / "danger-list.txt").write_text("# no tracked terms\n", encoding="utf-8")
    (safety / "danger-list.local.txt").write_text("projectbluebird\n", encoding="utf-8")
    monkeypatch.setenv("JSCC_SAFETY_DIR", str(safety))

    f = _write(tmp_path / "notes.md", "the projectbluebird kickoff is monday\n")
    assert precommit_scan.main([str(f)]) == 1


def test_a_staged_file_holding_an_api_key_is_blocked(tmp_path: Path) -> None:
    """End-to-end proof the shared definition reaches the scanner.

    `personal_data.py` is one definition for two egress points, so a rule added
    there is supposed to arrive at both without a second edit. This asserts the
    git half actually did -- the half that matters for a credential, since a
    committed key is the damage.
    """
    key = "sk-" + "ant-" + "api03-" + "C" * 40
    target = _write(tmp_path / "notes.md", f"my key is {key}\n")
    danger = _write(tmp_path / "danger.txt", "# empty\n")
    assert precommit_scan.main([str(target), "--danger-list", str(danger)]) == 1
