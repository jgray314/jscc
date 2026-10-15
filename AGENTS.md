# JSCC

Job-search pipeline tracker built around eval-backed LLM stages (extraction, fit scoring, routing, drafting). Python 3.12+, uv, pydantic v2, click, SQLite.

## Run and verify

- Setup on a fresh clone: `uv sync --all-extras --frozen`, then `git config core.hooksPath .githooks` (once; enables the commit-msg and pre-push hooks).
- Full check, same order as CI: `uv run ruff check . && uv run ruff format --check . && bash scripts/scan_tracked.sh && uv run mypy && uv run pytest -q` (about 25 seconds).
- On Windows with Git Bash, `uv` is in `~/.local/bin`: `export PATH="$HOME/.local/bin:$PATH"` first.
- Evals replay recorded responses: see `evals/README.md`. Never write proxy output into a recorded fixture.

## Where things are

- `FEATURES.json` is the verification ledger: each entry is a check gate with a command that proves it. `PROGRESS.md` is generated from it and lists those gates only. Product progress and open work are in the README "Status" and `docs/technical-reference.md` "Status".
- `decisions/` holds ADRs, `docs/design-principles.md` the locked decisions, `CHANGELOG.md` the build arc, `docs/gate-reviews.md` the review record.

## Rules

1. One active feature at a time: `python scripts/features.py activate <id>`, build, then `python scripts/features.py verify <id>`.
2. Never edit a feature's `state` or `evidence` by hand. Only `verify` can make it `passing`; `check` rejects a hand edit.
3. A feature with no runnable verify command is not specified yet. Write the test first and watch it fail.
4. Do not claim a test passes without running it in this turn. Paste real output.
5. Every model call goes through `sanitize_for_llm` then `send_to_llm`. Do not call a client directly.
6. Database opens go through `open_for_mode` only. Real data is gitignored; `data/synthetic.db` is the only tracked database.
7. When writing a comment or doc about a scanner false positive, describe the pattern's shape, never requote the literal.
8. A fix to a Critical or High finding needs proof: a test that fails without the fix, a mutation check, or a deletion proof.
9. A new dependency needs `uv lock` and a committed `uv.lock`. CI actions stay SHA-pinned.
10. No Co-Authored-By trailers. The commit-msg hook rejects them.

## End of a session

Run `python scripts/features.py clean`, then `python scripts/features.py progress`, then commit.
