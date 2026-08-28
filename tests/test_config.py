from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from jscc.config import (
    LoadError,
    Profile,
    StagesConfig,
    load_profile,
    load_stages,
    resolve_profile_path,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_CONFIG = REPO_ROOT / "config"


def test_sample_stages_loads() -> None:
    cfg = load_stages(SAMPLE_CONFIG / "stages.yaml")
    assert isinstance(cfg, StagesConfig)
    assert cfg.stages[0] == "identified"
    assert cfg.staleness_thresholds_days["applied"] == 14


def test_sample_profile_example_loads() -> None:
    prof = load_profile(SAMPLE_CONFIG / "profile.example.yaml")
    assert isinstance(prof, Profile)
    assert prof.experience_years == 12
    assert prof.comp_target.min_usd < prof.comp_target.max_usd


def test_resolve_profile_prefers_private(tmp_path: Path) -> None:
    (tmp_path / "profile.example.yaml").write_text(
        "display_name: Example\nrole_focus: [em]\nlevel_target: L6\n"
        "experience_years: 5\ncomp_target: {min_usd: 100, max_usd: 200}\n",
        encoding="utf-8",
    )
    (tmp_path / "profile.private.yaml").write_text(
        "display_name: Private\nrole_focus: [em]\nlevel_target: L6\n"
        "experience_years: 5\ncomp_target: {min_usd: 100, max_usd: 200}\n",
        encoding="utf-8",
    )
    resolved = resolve_profile_path(tmp_path)
    assert resolved.name == "profile.private.yaml"


def test_resolve_profile_falls_back_to_example(tmp_path: Path) -> None:
    (tmp_path / "profile.example.yaml").write_text(
        "display_name: Example\nrole_focus: [em]\nlevel_target: L6\n"
        "experience_years: 5\ncomp_target: {min_usd: 100, max_usd: 200}\n",
        encoding="utf-8",
    )
    resolved = resolve_profile_path(tmp_path)
    assert resolved.name == "profile.example.yaml"


def test_resolve_profile_none_present_raises(tmp_path: Path) -> None:
    with pytest.raises(LoadError, match="no profile config found"):
        resolve_profile_path(tmp_path)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(LoadError, match="not found"):
        load_stages(tmp_path / "nope.yaml")


def test_stages_missing_thresholds(tmp_path: Path) -> None:
    p = tmp_path / "stages.yaml"
    p.write_text(
        "stages: [applied, offer]\nstaleness_thresholds_days: {applied: 14}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="missing entries for.*offer"):
        load_stages(p)


def test_stages_unknown_threshold(tmp_path: Path) -> None:
    p = tmp_path / "stages.yaml"
    p.write_text(
        "stages: [applied]\nstaleness_thresholds_days: {applied: 14, mystery: 7}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="unknown stages.*mystery"):
        load_stages(p)


def test_stages_empty_stages_rejected(tmp_path: Path) -> None:
    p = tmp_path / "stages.yaml"
    p.write_text("stages: []\nstaleness_thresholds_days: {}\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_stages(p)


def test_profile_bad_type_rejected(tmp_path: Path) -> None:
    p = tmp_path / "profile.yaml"
    p.write_text(
        "display_name: x\n"
        "role_focus: [em]\n"
        "level_target: senior\n"
        "experience_years: 'twelve'\n"
        "comp_target: {min_usd: 100, max_usd: 200}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        load_profile(p)


def test_profile_comp_min_gt_max_rejected(tmp_path: Path) -> None:
    p = tmp_path / "profile.yaml"
    p.write_text(
        "display_name: x\n"
        "role_focus: [em]\n"
        "level_target: senior\n"
        "experience_years: 10\n"
        "comp_target: {min_usd: 500, max_usd: 100}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="min_usd must be <= max_usd"):
        load_profile(p)


def test_bad_yaml_raises_load_error(tmp_path: Path) -> None:
    p = tmp_path / "stages.yaml"
    p.write_text("stages: [applied\n", encoding="utf-8")  # missing close bracket
    with pytest.raises(LoadError, match="yaml parse error"):
        load_stages(p)


def test_top_level_not_mapping_rejected(tmp_path: Path) -> None:
    p = tmp_path / "stages.yaml"
    p.write_text("- item\n- other\n", encoding="utf-8")
    with pytest.raises(LoadError, match="expected top-level mapping"):
        load_stages(p)
