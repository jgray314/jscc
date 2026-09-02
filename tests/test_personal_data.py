"""Tests for the shared personal-data definition (D7 M3 + M5).

This module is on the scanner's exclude list (see `scripts/scan_tracked.sh`)
because it deliberately contains email- and phone-shaped fixtures.
"""

from __future__ import annotations

from pathlib import Path

from jscc.personal_data import (
    DANGER_TOKEN,
    EMAIL_TOKEN,
    PHONE_TOKEN,
    find_personal,
    load_danger_list,
    redact,
)

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
