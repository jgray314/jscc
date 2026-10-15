"""The pre-capture sizing gate: each check fires on the shape it is named for.

A capture round costs one fresh chat per case, so a round found undersized,
unbalanced or unable to discriminate after the fact is money already spent (jd
extraction shipped at n=15, n=10 and n=12 before being resized). These tests pin
that every named check fires on a synthetic round built to trip exactly that
check, that a sound round trips none, and that the tracked suites are sound.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import eval_sizing  # noqa: E402


def _routing_case(i: int, label: str, note: str | None = None) -> dict:
    return {
        "id": f"r-{i:02d}",
        "application": {"id": f"app-{i}", "title": f"Role {i}", "company": f"Co{i}"},
        "history": [
            {
                "id": f"h-{i}",
                "application_id": f"app-{i}",
                "type": "note",
                "notes": note
                or f"unique note number {i} " + " ".join(f"w{i}x{j}" for j in range(12)),
            }
        ],
        "expected_classification": label,
    }


def _routing_round(n_routine: int = 12, n_non: int = 12) -> list[dict]:
    cases = [_routing_case(i, "routine") for i in range(n_routine)]
    cases += [_routing_case(100 + i, "non_routine") for i in range(n_non)]
    return cases


def _fit_case(i: int, lo: float, hi: float, text: str | None = None) -> dict:
    return {
        "id": f"f-{i:02d}",
        "raw_jd_text": text or f"posting {i} " + " ".join(f"t{i}y{j}" for j in range(15)),
        "extracted_jd": {"title": f"T{i}"},
        "profile": {"display_name": "x"},
        "min_score": lo,
        "max_score": hi,
    }


def _fit_round(n: int = 30) -> list[dict]:
    bands = [(5, 40), (45, 75), (70, 100)]
    return [_fit_case(i, *bands[i % 3]) for i in range(n)]


def _jd_case(i: int, expected: dict | None = None, raw: str | None = None) -> dict:
    return {
        "id": f"case-{i:02d}",
        "raw_jd": raw or f"Posting {i} " + " ".join(f"p{i}q{j}" for j in range(20)),
        "expected": expected
        if expected is not None
        else {"title": f"T{i}", "level": "senior", "must_have_skills": ["sql"]},
    }


def _names(findings: list[eval_sizing.Finding]) -> set[str]:
    return {f.check for f in findings}


def test_a_sound_round_trips_no_check() -> None:
    assert eval_sizing.check_round("routing", _routing_round()) == []
    assert eval_sizing.check_round("fit_scoring", _fit_round()) == []
    assert eval_sizing.check_round("jd_extraction", [_jd_case(i) for i in range(30)]) == []


# --- undersized-round -----------------------------------------------------------


def test_undersized_round_fires_below_the_standard_error_limit() -> None:
    findings = eval_sizing.check_round("fit_scoring", _fit_round(12))
    assert "undersized-round" in _names(findings)
    message = next(f.message for f in findings if f.check == "undersized-round")
    assert "n=12" in message and "at least" in message


def test_undersized_round_names_the_case_count_that_would_clear_it() -> None:
    needed = eval_sizing.min_cases_for_bar(0.80)
    assert "undersized-round" not in _names(
        eval_sizing.check_round("fit_scoring", _fit_round(needed))
    )
    assert "undersized-round" in _names(
        eval_sizing.check_round("fit_scoring", _fit_round(needed - 1))
    )


def test_a_higher_bar_needs_fewer_cases_and_a_lower_bar_more() -> None:
    assert eval_sizing.min_cases_for_bar(0.85) < eval_sizing.min_cases_for_bar(0.80)
    assert eval_sizing.min_cases_for_bar(0.75) > eval_sizing.min_cases_for_bar(0.80)


# --- class-imbalance ------------------------------------------------------------


def test_class_imbalance_fires_when_one_class_dominates() -> None:
    findings = eval_sizing.check_round("routing", _routing_round(22, 4))
    assert "class-imbalance" in _names(findings)


def test_class_imbalance_majority_cap_fires_with_both_classes_above_the_floor() -> None:
    # 22 of 30 is 73%: each class clears the per-class floor, so only the cap can fire.
    findings = eval_sizing.check_round("routing", _routing_round(22, 8))
    message = next(f.message for f in findings if f.check == "class-imbalance")
    assert "over the 70% cap" in message and "under the floor" not in message


def test_class_imbalance_fires_when_a_class_is_too_thin_to_gate_on() -> None:
    # 4 of 14 is under the 70% majority cap but under the per-class floor.
    findings = eval_sizing.check_round("routing", _routing_round(10, 4))
    assert "class-imbalance" in _names(findings)


def test_class_imbalance_fires_when_a_class_is_absent() -> None:
    findings = eval_sizing.check_round("routing", _routing_round(26, 0))
    assert "class-imbalance" in _names(findings)


def test_composition_must_ask_cases_count_as_a_class() -> None:
    def comp(i: int, ask: bool) -> dict:
        return {
            "id": f"c-{i:02d}",
            "application": {"title": f"R{i}"},
            "history": [{"notes": f"note {i} " + " ".join(f"z{i}{j}" for j in range(12))}],
            "intent": f"intent {i} " + " ".join(f"k{i}{j}" for j in range(8)),
            "must_include": [["x"]],
            "expect_needs_input": ask,
        }

    sound = [comp(i, i < 8) for i in range(28)]
    assert eval_sizing.check_round("composition", sound) == []
    thin = [comp(i, i < 1) for i in range(28)]
    assert "class-imbalance" in _names(eval_sizing.check_round("composition", thin))


# --- duplicate-case-id / duplicate-input / near-duplicate-input -----------------


def test_duplicate_case_id_fires() -> None:
    cases = _routing_round()
    cases[3]["id"] = cases[2]["id"]
    findings = eval_sizing.check_round("routing", cases)
    assert "duplicate-case-id" in _names(findings)
    assert cases[2]["id"] in next(f for f in findings if f.check == "duplicate-case-id").case_ids


def test_duplicate_input_fires_ignoring_ids_case_and_whitespace() -> None:
    cases = _routing_round()
    twin = copy.deepcopy(cases[0])
    twin["id"] = "r-twin"
    twin["application"]["id"] = "app-twin"
    twin["history"][0]["notes"] = "  " + twin["history"][0]["notes"].upper() + "\n"
    findings = eval_sizing.check_round("routing", [*cases, twin])
    dup = next(f for f in findings if f.check == "duplicate-input")
    assert set(dup.case_ids) == {"r-00", "r-twin"}


def test_near_duplicate_input_fires_on_one_word_changed() -> None:
    cases = _routing_round()
    near = copy.deepcopy(cases[0])
    near["id"] = "r-near"
    near["history"][0]["notes"] = near["history"][0]["notes"].replace("w0x3", "different")
    findings = eval_sizing.check_round("routing", [*cases, near])
    names = _names(findings)
    assert "near-duplicate-input" in names and "duplicate-input" not in names


def test_distinct_inputs_are_not_near_duplicates() -> None:
    assert "near-duplicate-input" not in _names(
        eval_sizing.check_round("routing", _routing_round())
    )


# --- non-discriminating-case ----------------------------------------------------


def test_constant_answer_baseline_fires_when_one_score_satisfies_most_bands() -> None:
    cases = [_fit_case(i, 40, 80) for i in range(30)]
    findings = eval_sizing.check_round("fit_scoring", cases)
    assert "constant-answer-baseline" in _names(findings)


def test_constant_answer_baseline_is_quiet_for_separated_bands() -> None:
    assert "constant-answer-baseline" not in _names(
        eval_sizing.check_round("fit_scoring", _fit_round())
    )


def test_a_near_twin_with_the_opposite_expectation_is_a_contrast_pair_not_a_dup() -> None:
    cases = _routing_round()
    twin = copy.deepcopy(cases[0])
    twin["id"] = "r-contrast"
    twin["history"][0]["notes"] = twin["history"][0]["notes"].replace("w0x3", "different")
    twin["expected_classification"] = "non_routine"
    assert "near-duplicate-input" not in _names(eval_sizing.check_round("routing", [*cases, twin]))


def test_fit_band_wider_than_the_limit_cannot_discriminate() -> None:
    cases = _fit_round()
    cases[5]["min_score"], cases[5]["max_score"] = 10, 95
    hit = next(
        f
        for f in eval_sizing.check_round("fit_scoring", cases)
        if f.check == "non-discriminating-case"
    )
    assert hit.case_ids == (cases[5]["id"],)


def test_inverted_fit_band_cannot_be_satisfied() -> None:
    cases = _fit_round()
    cases[6]["min_score"], cases[6]["max_score"] = 80, 60
    assert "non-discriminating-case" in _names(eval_sizing.check_round("fit_scoring", cases))


def test_extraction_case_with_nothing_graded_cannot_discriminate() -> None:
    cases = [_jd_case(i) for i in range(30)]
    cases[7]["expected"] = {
        "title": None,
        "company": None,
        "level": None,
        "remote_policy": None,
        "comp_band": None,
        "location": None,
        "must_have_skills": [],
    }
    hit = next(
        f
        for f in eval_sizing.check_round("jd_extraction", cases)
        if f.check == "non-discriminating-case"
    )
    assert hit.case_ids == ("case-07",)


def test_extraction_case_with_one_graded_field_is_enough() -> None:
    cases = [_jd_case(i) for i in range(30)]
    cases[7]["expected"] = {"level": "senior"}
    assert "non-discriminating-case" not in _names(eval_sizing.check_round("jd_extraction", cases))


def test_composition_case_with_no_expectations_cannot_discriminate() -> None:
    def comp(i: int, include: list, forbid: list) -> dict:
        return {
            "id": f"c-{i:02d}",
            "application": {"title": f"R{i}"},
            "history": [{"notes": f"note {i} " + " ".join(f"z{i}{j}" for j in range(12))}],
            "intent": f"intent {i} " + " ".join(f"k{i}{j}" for j in range(8)),
            "must_include": include,
            "must_not_include": forbid,
            "expect_needs_input": i < 8,
        }

    cases = [comp(i, [["x"]], []) for i in range(28)]
    cases[9] = comp(9, [], [])
    hit = next(
        f
        for f in eval_sizing.check_round("composition", cases)
        if f.check == "non-discriminating-case"
    )
    assert hit.case_ids == ("c-09",)


# --- paste-ceiling --------------------------------------------------------------


def test_paste_ceiling_fires_on_an_input_too_large_to_paste_into_a_chat() -> None:
    cases = _fit_round()
    cases[2]["raw_jd_text"] = "word " * (eval_sizing.CHAT_PASTE_CEILING_CHARS // 5 + 10)
    hit = next(
        f for f in eval_sizing.check_round("fit_scoring", cases) if f.check == "paste-ceiling"
    )
    assert hit.case_ids == (cases[2]["id"],)


def test_paste_ceiling_does_not_count_the_expected_answer() -> None:
    cases = [_jd_case(i) for i in range(30)]
    cases[1]["expected"]["responsibilities_summary"] = "x " * eval_sizing.CHAT_PASTE_CEILING_CHARS
    assert "paste-ceiling" not in _names(eval_sizing.check_round("jd_extraction", cases))


def test_the_chat_ceiling_sits_below_the_products_own_paste_limit() -> None:
    from jscc.ingest_logic import MAX_PASTE_BYTES

    assert eval_sizing.CHAT_PASTE_CEILING_CHARS < MAX_PASTE_BYTES


# --- plumbing -------------------------------------------------------------------


def test_every_check_has_a_name_a_test_here_and_a_message() -> None:
    source = Path(__file__).read_text(encoding="utf-8")
    for name in eval_sizing.CHECK_NAMES:
        assert f'"{name}"' in source, f"no test names the {name} check"


def test_an_unknown_suite_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown suite"):
        eval_sizing.check_round("nope", [])


@pytest.mark.parametrize("suite", sorted(eval_sizing.SUITES))
def test_the_tracked_suite_passes_the_gate(suite: str) -> None:
    findings = eval_sizing.check_tracked_suite(suite)
    waived = [f for f in findings if f.check not in eval_sizing.TRACKED_WAIVERS.get(suite, ())]
    assert waived == [], eval_sizing.format_findings(suite, waived)


def test_the_cli_exits_nonzero_on_a_failing_round_and_names_the_check(tmp_path: Path) -> None:
    bad = tmp_path / "cases.json"
    bad.write_text(json.dumps(_fit_round(8)), encoding="utf-8")
    run = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "eval_sizing.py"),
            "fit_scoring",
            "--cases",
            str(bad),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert run.returncode == 1
    assert "undersized-round" in run.stdout


def test_the_cli_exits_zero_on_a_sound_round(tmp_path: Path) -> None:
    good = tmp_path / "cases.json"
    good.write_text(json.dumps(_fit_round()), encoding="utf-8")
    run = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "eval_sizing.py"),
            "fit_scoring",
            "--cases",
            str(good),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert run.returncode == 0, run.stdout
