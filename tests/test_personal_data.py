"""Tests for the shared personal-data definition (D7 M3 + M5).

This module is on the scanner's exclude list (see `scripts/scan_tracked.sh`)
because it deliberately contains email- and phone-shaped fixtures.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jscc.personal_data import (
    CREDENTIAL_TOKEN,
    DANGER_TOKEN,
    EMAIL_TOKEN,
    PHONE_TOKEN,
    SAFETY_DIR_ENV_VAR,
    SafetyConfigError,
    default_danger_terms,
    find_personal,
    load_danger_list,
    redact,
)

# Key-shaped fixtures are assembled at runtime rather than written as literals.
# This file is on the scanner's exclude list today, but a fixture that depends
# on staying excluded is a fixture that breaks the day the list is tidied -- and
# the whole point of these tests is that a key-shaped string does not survive a
# commit. Same convention the model id in `llm_client` follows, for the same
# reason.
_KEY_PREFIX = "sk-" + "ant-"
FAKE_KEY = _KEY_PREFIX + "api03-" + "A" * 40
FAKE_ADMIN_KEY = _KEY_PREFIX + "admin01-" + "B" * 40
# A key whose body carries a phone-length digit run -- the collision that makes
# redaction order load-bearing.
FAKE_KEY_WITH_DIGITS = _KEY_PREFIX + "api03-" + "123456789012" + "xyz"

RECRUITER_EMAIL = "dana.reyes@riftcloud.example"
RECRUITER_PHONE = "(415) 555-0134"
INTL_PHONE = "+44.20.7946.0018"


# ---- redact: the M5 rewrite half -------------------------------------------


def test_redacts_email() -> None:
    out = redact(f"Reach me at {RECRUITER_EMAIL} anytime.")
    assert RECRUITER_EMAIL not in out
    assert EMAIL_TOKEN in out


def test_redacts_us_phone() -> None:
    out = redact(f"Call {RECRUITER_PHONE} today.")
    assert "555-0134" not in out
    assert PHONE_TOKEN in out


def test_redacts_international_dotted_phone() -> None:
    out = redact(f"Ring {INTL_PHONE} instead.")
    assert "7946" not in out
    assert PHONE_TOKEN in out


def test_redacts_email_before_phone_so_numeric_locals_survive_intact() -> None:
    """A numeric local-part is phone-shaped. If phones went first it would be
    chewed up and the remaining fragment would no longer match the email rule,
    leaking the domain. Emails must be redacted first."""
    numeric = "12345678901@riftcloud.example"
    out = redact(f"mail {numeric} end")
    assert "riftcloud.example" not in out
    assert out == f"mail {EMAIL_TOKEN} end"


def test_does_not_redact_iso_dates() -> None:
    """8 digits is below the E.164 floor — dates must survive."""
    text = "Applied on 2026-08-28 and heard back 2026-09-01."
    assert redact(text) == text


def test_does_not_redact_ordinary_prose() -> None:
    text = "Senior Engineer, 5+ years of Python and distributed systems."
    assert redact(text) == text


def test_redacts_danger_list_terms_case_insensitively() -> None:
    out = redact("Referred by ExRealCompanyName Corp.", danger_terms=["exrealcompanyname corp"])
    assert "ExRealCompanyName" not in out
    assert DANGER_TOKEN in out


def test_name_roles_become_role_tokens() -> None:
    """D7 M5's literal wording: contact names to role tokens."""
    out = redact("Spoke with Dana Reyes today.", name_roles={"Dana Reyes": "recruiter"})
    assert "Dana Reyes" not in out
    assert "[contact:recruiter]" in out


def test_name_roles_match_case_insensitively() -> None:
    out = redact("spoke with dana reyes today", name_roles={"Dana Reyes": "recruiter"})
    assert "[contact:recruiter]" in out


def test_redacts_all_three_classes_in_one_pass() -> None:
    text = f"Dana Reyes, {RECRUITER_EMAIL}, {RECRUITER_PHONE}, via ExampleCorp"
    out = redact(
        text, danger_terms=["examplecorp"], name_roles={"Dana Reyes": "recruiter"}
    )
    for leaked in ("Dana Reyes", RECRUITER_EMAIL, "555-0134", "ExampleCorp"):
        assert leaked not in out


def test_empty_string_is_returned_unchanged() -> None:
    assert redact("") == ""


def test_redaction_is_idempotent() -> None:
    once = redact(f"{RECRUITER_EMAIL} and {RECRUITER_PHONE}")
    assert redact(once) == once


# ---- find_personal: the M3 detection half ----------------------------------


def test_find_personal_flags_email_and_phone() -> None:
    reasons = {r for r, _ in find_personal(f"{RECRUITER_EMAIL} {RECRUITER_PHONE}", [])}
    assert reasons == {"email-pattern", "phone-pattern"}


def test_find_personal_ignores_clean_line() -> None:
    assert find_personal("Senior Engineer, Python, remote.", []) == []


def test_find_personal_flags_danger_term() -> None:
    hits = find_personal("Referred by AcmeCo", ["acmeco"])
    assert [r for r, _ in hits] == ["danger-list"]


# ---- the shared-definition property ----------------------------------------


def test_scanner_and_sanitizer_agree_on_what_is_personal() -> None:
    """Gate finding C1: the pre-commit scanner (M3) blocked this content from
    git while the sanitizer (M5) forwarded it verbatim to an LLM. Both now
    read the same rules, so anything the scanner flags, redact() removes."""
    text = f"Dana Reyes, {RECRUITER_EMAIL}, {RECRUITER_PHONE}"
    flagged = [match for _, match in find_personal(text, [])]
    assert flagged, "fixture should trip the scanner"

    redacted = redact(text)
    for match in flagged:
        assert match not in redacted
    assert find_personal(redacted, []) == []


def test_load_danger_list_skips_comments_and_blanks(tmp_path: Path) -> None:
    p = tmp_path / "danger.txt"
    p.write_text("# comment\n\nAcmeCo\n  SpacedTerm  \n", encoding="utf-8")
    assert load_danger_list(p) == ["acmeco", "spacedterm"]


def test_load_danger_list_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_danger_list(tmp_path / "nope.txt") == []


# ---- paths are anchored, not cwd-relative (rerun-gate H-1) ------------------
#
# These chdir for real rather than mocking the path, because the bug was
# precisely that the real resolution depended on the real working directory.
# A test that patches the path away cannot see it -- the same shape of gap the
# rerun gate found in the M5 guard tests.


def test_danger_terms_load_from_outside_the_repo_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """H-1: `default_danger_terms()` returned [] from any other directory,
    silently, while email/phone redaction kept working so nothing looked wrong.
    That is the load-bearing half of the C1 guarantee: the scanner runs from the
    repo root, the sanitizer runs wherever the user is."""
    monkeypatch.delenv(SAFETY_DIR_ENV_VAR, raising=False)
    from_root = default_danger_terms()
    monkeypatch.chdir(tmp_path)
    assert default_danger_terms() == from_root


def test_redaction_of_a_danger_term_survives_a_foreign_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The property that actually matters, asserted end to end."""
    safety = tmp_path / "safety"
    safety.mkdir()
    (safety / "danger-list.txt").write_text("riftcloud\n", encoding="utf-8")
    monkeypatch.setenv(SAFETY_DIR_ENV_VAR, str(safety))
    monkeypatch.chdir(tmp_path)
    terms = default_danger_terms()
    assert redact("the riftcloud migration", danger_terms=terms) == "the [redacted] migration"


def test_a_stray_dot_safety_in_the_cwd_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The old behaviour read whatever .safety happened to be underfoot, which
    is how the scanner and the sanitizer could disagree about the same term."""
    monkeypatch.delenv(SAFETY_DIR_ENV_VAR, raising=False)
    stray = tmp_path / ".safety"
    stray.mkdir()
    (stray / "danger-list.txt").write_text("stray-term-that-must-not-load\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert "stray-term-that-must-not-load" not in default_danger_terms()


def test_explicit_safety_dir_that_does_not_exist_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An override that does not resolve means the operator believes a list is
    loaded when none is. Fail loudly -- silence is the bug being replaced."""
    monkeypatch.setenv(SAFETY_DIR_ENV_VAR, str(tmp_path / "nope"))
    with pytest.raises(SafetyConfigError):
        default_danger_terms()


def test_missing_default_safety_dir_warns_rather_than_going_quiet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(SAFETY_DIR_ENV_VAR, str(tmp_path))  # exists but empty -> no warning
    assert default_danger_terms() == []
    monkeypatch.setattr("jscc.personal_data.safety_dir", lambda: tmp_path / "gone")
    with pytest.warns(RuntimeWarning, match="danger-list"):
        assert default_danger_terms() == []


# ---- credentials ------------------------------------------------------------
#
# A credential is not personal data, and these tests keep that distinction
# visible: it has its own reason label and its own token. What it shares with
# the rest of this module is the boundary -- it must not cross an egress point.
# The prompt for adding it was concrete: a real API key was about to exist on
# this machine, and nothing here would have stopped it being committed.


def test_an_api_key_is_flagged_with_its_own_reason() -> None:
    hits = find_personal(f"export ANTHROPIC_API_KEY={FAKE_KEY}", [])
    assert ("credential-pattern", FAKE_KEY) in hits


def test_admin_keys_are_covered_too() -> None:
    """Admin keys open more doors than a workspace key, not fewer."""
    hits = find_personal(f"key: {FAKE_ADMIN_KEY}", [])
    assert [reason for reason, _ in hits] == ["credential-pattern"]


def test_an_api_key_is_redacted() -> None:
    assert redact(f"use {FAKE_KEY} to authenticate") == (
        f"use {CREDENTIAL_TOKEN} to authenticate"
    )


def test_a_key_body_with_a_phone_shaped_run_is_not_mangled_by_the_phone_rule() -> None:
    """Why credentials are redacted before phones.

    A key body is alphanumeric with dashes, so a digit run inside one sits in
    the phone rule's window. If phones went first the run would become a phone
    token and leave a string that is still most of a key and no longer matches
    `CREDENTIAL_RE` -- a partial redaction that reads as a successful one.
    Verified by moving the credential substitution after `_redact_phones` and
    watching this fail.
    """
    out = redact(f"key {FAKE_KEY_WITH_DIGITS} here")
    assert out == f"key {CREDENTIAL_TOKEN} here"
    assert PHONE_TOKEN not in out


def test_the_credential_rule_does_not_fire_on_ordinary_text() -> None:
    """Narrow on purpose. A rule that cries wolf is a rule someone turns off."""
    for benign in (
        "the sk-ant- prefix identifies an Anthropic key",
        "sk-ant-short",
        "scikit-learn and pandas",
        "",
    ):
        assert not [r for r, _ in find_personal(benign, []) if r == "credential-pattern"]
        assert redact(benign) == benign
