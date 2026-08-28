# Changelog

## [Unreleased]

### A1 — Repo scaffold + config loader
- Bootstrapped `jscc` Python package under the portfolio repo.
- Added pydantic-based config models for `stages.yaml` and `profile.yaml`.
- Wired `python -m jscc validate-config` — exits 0 on valid config, non-zero on broken.
- Unit tests: valid load, missing required field, bad type, unknown-field tolerance.
- Decisions: pydantic v2 over jsonschema (typed models double as runtime validators + docs); JSCC ships as its own repo linked from the `ai-portfolio` index.
