"""Direct tests for `jscc/ingest_logic.py`'s title/company verification
(`unverified_fields`) and the `needs_confirmation` / `extracted_override`
halves of `extract_and_create_application` it gates.

Composition trusts `Application.title`/`company` with no raw text alongside
it to catch a wrong one (the extraction-error-propagation probe), unlike
scoring, which gets both. This is the check that stands in for that missing
insulation, at ingest, where a human is available: blocking confirmation,
not a silent flag or a DLQ-reuse -- neither survives contact with the
lessons this project's own gates have already paid for (see
docs/lessons-learned.md lesson 1 on why a flag is not a control).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jscc.ingest_logic import (
    ExtractOutcome,
    extract_and_create_application,
    unverified_fields,
)
from jscc.mode import ENV_VAR, Mode
from jscc.models import ExtractedJD
from jscc.storage import list_applications, open_for_mode


def _extracted(**overrides) -> ExtractedJD:
    fields = dict(
        title="Staff Backend Engineer",
        company="Acme Corp",
        level="staff",
        comp_band=None,
        location=None,
        remote_policy=None,
        must_have_skills=[],
        responsibilities_summary="s",
    )
    fields.update(overrides)
    return ExtractedJD(**fields)


# ---- unverified_fields -------------------------------------------------------


def test_flags_a_title_the_raw_text_never_names() -> None:
    extracted = _extracted(title="Staff Backend Engineer", company="Acme Corp")
    raw = "We are hiring at Acme Corp. Come build with us."
    assert unverified_fields(extracted, raw, title_override=None, company_override=None) == {
        "title"
    }


def test_flags_a_company_the_raw_text_never_names() -> None:
    extracted = _extracted(title="Staff Backend Engineer", company="Acme Corp")
    raw = "Staff Backend Engineer role, employer withheld for now."
    assert unverified_fields(extracted, raw, title_override=None, company_override=None) == {
        "company"
    }


def test_a_null_company_is_an_absence_not_a_claim_to_verify() -> None:
    extracted = _extracted(title="Staff Backend Engineer", company=None)
    raw = "Staff Backend Engineer role, employer withheld for now."
    assert unverified_fields(extracted, raw, title_override=None, company_override=None) == set()


def test_an_override_pre_empts_verification_of_its_own_field_only() -> None:
    extracted = _extracted(title="Staff Backend Engineer", company="Acme Corp")
    raw = "a job description"
    assert unverified_fields(extracted, raw, title_override="X", company_override=None) == {
        "company"
    }
    assert unverified_fields(extracted, raw, title_override=None, company_override="Y") == {"title"}
    assert unverified_fields(extracted, raw, title_override="X", company_override="Y") == set()


def test_forgives_casing_and_hyphen_slash_whitespace_noise() -> None:
    extracted = _extracted(title="Infrastructure-as-Code Lead", company="Acme Corp")
    raw = "  ACME   corp is hiring an  infrastructure as code   lead. "
    assert unverified_fields(extracted, raw, title_override=None, company_override=None) == set()


def test_both_fields_can_fail_at_once() -> None:
    extracted = _extracted(title="Staff Backend Engineer", company="Acme Corp")
    assert unverified_fields(
        extracted, "totally unrelated text", title_override=None, company_override=None
    ) == {"title", "company"}


# ---- extract_and_create_application -------------------------------------------


@pytest.fixture
def conn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    connection = open_for_mode(Mode.synthetic, tmp_path)
    yield connection
    connection.close()


def _patch_extraction(monkeypatch: pytest.MonkeyPatch, extracted: ExtractedJD) -> None:
    monkeypatch.setattr("jscc.ingest_logic.extract_jd", lambda raw_text, conn=None: extracted)


def test_needs_confirmation_stores_nothing(conn, monkeypatch: pytest.MonkeyPatch) -> None:
    extracted = _extracted(title="Staff Backend Engineer", company="Acme Corp")
    _patch_extraction(monkeypatch, extracted)

    result = extract_and_create_application(
        conn,
        raw_text="a job description",
        source_url=None,
        company_override=None,
        fallback_company="(pasted)",
        fallback_title=None,
    )

    assert result.outcome is ExtractOutcome.needs_confirmation
    assert result.extracted == extracted
    assert result.unverified_fields == {"title", "company"}
    assert result.application_id is None
    assert list_applications(conn) == []


def test_extracted_override_skips_reextraction_and_verification(
    conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller replaying a confirmed extraction back in must not trigger a
    second model call or a second verification check -- both would risk a
    different extraction than the one a human already confirmed."""
    extracted = _extracted(title="Staff Backend Engineer", company="Acme Corp")

    def _fail_if_called(raw_text, conn=None):
        raise AssertionError("extract_jd must not be called when extracted_override is given")

    monkeypatch.setattr("jscc.ingest_logic.extract_jd", _fail_if_called)

    result = extract_and_create_application(
        conn,
        raw_text="a job description",
        source_url=None,
        company_override=None,
        fallback_company="(pasted)",
        fallback_title=None,
        extracted_override=extracted,
    )

    assert result.outcome is ExtractOutcome.created
    apps = list_applications(conn)
    assert len(apps) == 1
    assert apps[0].title == "Staff Backend Engineer"
    assert apps[0].company == "Acme Corp"
    assert apps[0].extracted_jd == extracted.model_dump()


def test_title_override_both_preempts_the_check_and_sets_the_stored_title(
    conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    extracted = _extracted(title="Staff Backend Engineer", company="Acme Corp")
    _patch_extraction(monkeypatch, extracted)

    result = extract_and_create_application(
        conn,
        raw_text="a job description",
        source_url=None,
        company_override="Acme Corp",
        title_override="Corrected Title",
        fallback_company="(pasted)",
        fallback_title=None,
    )

    assert result.outcome is ExtractOutcome.created
    assert result.application.title == "Corrected Title"
    # The stored extracted_jd is what the model actually returned, not the
    # override -- the override corrects the Application, not the record of
    # what extraction said.
    assert result.application.extracted_jd == extracted.model_dump()


def test_a_verified_extraction_creates_in_one_call(conn, monkeypatch: pytest.MonkeyPatch) -> None:
    extracted = _extracted(title="Backend Engineer", company="Acme")
    _patch_extraction(monkeypatch, extracted)

    result = extract_and_create_application(
        conn,
        raw_text="Acme is hiring a Backend Engineer to join the team.",
        source_url=None,
        company_override=None,
        fallback_company="(pasted)",
        fallback_title=None,
    )

    assert result.outcome is ExtractOutcome.created
    assert list_applications(conn)[0].title == "Backend Engineer"
