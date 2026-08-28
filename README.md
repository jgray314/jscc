# JSCC — Job Search Command Center

An agentic pipeline tracker: ingest job descriptions, score fit against a profile, draft follow-ups (routine cases only), and surface stale opportunities. Every LLM stage is eval-gated.

Part of the [ai-portfolio](https://github.com/jgray314/ai-portfolio) index.

## Status

Early scaffolding — Phase A (foundations) in progress. See [CHANGELOG.md](CHANGELOG.md).

## Development

```bash
uv sync
uv run python -m jscc validate-config
uv run pytest
```
