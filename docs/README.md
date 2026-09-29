# Docs

What's in this folder and why, for a reader who landed here directly rather than through the [main README](../README.md).

## Core narrative and reference

The docs the main README's [Start here](../README.md#start-here-six-things-worth-reading-first) section points at, in the order it recommends:

- [how-i-built-this.md](how-i-built-this.md) — the narrative: why JSCC is shaped the way it is, what the eval rounds showed, what building it with an AI agent looked like.
- [technical-reference.md](technical-reference.md) — architecture diagram, repo layout, development commands, and the full phase-by-phase status table.
- [design-principles.md](design-principles.md) — the ten locked decisions (D1–D10) every slice was judged against, each with the rejected alternative.
- [threat-model.md](threat-model.md) — one page on what the tool protects, from whom, and what's still open (T1–T13).
- [gate-reviews.md](gate-reviews.md) — how the two-lens phase-boundary review works, and eight worked findings, including where it was wrong.
- [lessons-learned.md](lessons-learned.md) — what building this with AI assistance taught, evidenced against code, tests and commits.

Eval figures and round history live in [../evals/README.md](../evals/README.md), not here — none of the docs above repeat them.

## Supporting artifacts

Working documents behind a specific claim or slice, not narrative reading:

- [video-script.md](video-script.md) — the script for the F2 walkthrough video the README promises but hasn't shipped yet.
- [smoke-test-results.md](smoke-test-results.md) — the B3b real-URL smoke test log (`scripts/smoke_fetch.py`, not CI-gated) referenced from [technical-reference.md](technical-reference.md#repo-layout).
- [demo-fixtures/](demo-fixtures) — the redacted profile and hybridized posting used for the demo video and README samples.
