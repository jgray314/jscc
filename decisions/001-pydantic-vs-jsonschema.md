# ADR 001: pydantic v2 for config validation

**Status:** Accepted (2026-08-28, Slice A1)

## Context

JSCC needs to validate two YAML config files (`stages.yaml`, `profile.yaml`) at startup and to type-annotate them for the rest of the codebase. The schema will grow as later slices add more configuration surface.

## Decision

Use **pydantic v2** models as both the runtime validator and the typed representation carried through the codebase.

## Alternatives considered

- **`jsonschema` + `TypedDict`.** Familiar and lightweight, but forces two sources of truth (schema JSON + Python types) that drift. Rejected.
- **Hand-rolled `dataclass` + manual validation.** Zero dependency cost but reinvents error messages and cross-field checks. Rejected.
- **`attrs` + `cattrs`.** Valid, but pydantic is more idiomatic for AI-adjacent Python (LLM function-calling libraries, LangChain-family tooling) and produces a stronger "I know the ecosystem" signal for the target-role reader.

## Consequences

- Positive: `Profile` and `StagesConfig` are directly usable by later stages that will pass them into LLM calls (D9 scorer, D10 router).
- Positive: pydantic error messages are read-good on `validate-config` output.
- Cost: pydantic v2 is a non-trivial runtime dep. Acceptable given projected usage.
- Revisit if: schema stabilizes and we need to publish a JSON Schema for external consumers — pydantic can emit one, so this stays cheap.
