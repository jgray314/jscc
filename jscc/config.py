from __future__ import annotations

from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, Field, model_validator


class StagesConfig(BaseModel):
    stages: Annotated[list[str], Field(min_length=1)]
    staleness_thresholds_days: dict[str, int]

    @model_validator(mode="after")
    def _thresholds_cover_stages(self) -> "StagesConfig":
        missing = [s for s in self.stages if s not in self.staleness_thresholds_days]
        if missing:
            raise ValueError(f"staleness_thresholds_days missing entries for: {missing}")
        unknown = [s for s in self.staleness_thresholds_days if s not in self.stages]
        if unknown:
            raise ValueError(f"staleness_thresholds_days references unknown stages: {unknown}")
        return self


class CompRange(BaseModel):
    min_usd: int
    max_usd: int

    @model_validator(mode="after")
    def _min_le_max(self) -> "CompRange":
        if self.min_usd > self.max_usd:
            raise ValueError("min_usd must be <= max_usd")
        return self


class Profile(BaseModel):
    display_name: str
    role_focus: Annotated[list[str], Field(min_length=1)]
    level_target: str
    experience_years: int
    comp_target: CompRange
    must_haves: list[str] = []
    deal_breakers: list[str] = []
    style_samples: list[str] = []


class LoadError(Exception):
    pass


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise LoadError(f"config file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise LoadError(f"yaml parse error in {path}: {e}") from e
    if not isinstance(raw, dict):
        raise LoadError(f"expected top-level mapping in {path}, got {type(raw).__name__}")
    return raw


def load_stages(path: Path) -> StagesConfig:
    return StagesConfig.model_validate(_read_yaml(path))


def load_profile(path: Path) -> Profile:
    return Profile.model_validate(_read_yaml(path))
