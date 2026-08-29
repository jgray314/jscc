# Design principles

Ten decisions locked at the top of the JSCC project. Each records what was chosen, why, and the alternative considered. They are the reference for judging every subsequent slice.

The ADRs in `decisions/` extend specific principles with implementation-level detail; the principles themselves are the durable frame.

## D1 — Audience: technical hiring loops broadly

Not just engineering managers. EM/SWE/DE/MLE loops share enough of the pipeline shape that a tool built for EM applications generalizes cleanly. EM is the initial modeling frame.

## D2 — Vocabulary: job-search-specific, not generic pipeline

Domain nouns (`Application`, `Contact`, `Stage`, `Interaction`) rather than abstract pipeline terminology. Reusability-by-abstraction is premature; product focus is stronger signal for a portfolio piece.

## D3 — No RAG in the drafter for v1

Drafter uses in-prompt style samples instead of a retrieval layer. Parked for revisit if coverage gaps surface post-v1. A retrieval layer at v1 would be architecture ahead of evidence.

## D4 — Video walkthrough, not a live hosted demo

BYOK live demos are deferred. The build story carries the signal; a hosted service is scope inflation for a portfolio piece.

## D5 — LLM cost/latency instrumentation lands at the call site in Phase A

Every LLM call is instrumented from day one, via a decorator. Ledger and reporting come later (Phase C), but the data is captured from the first call. Retrofit-later would produce sparse data.

## D6 — JD fetching is a real product problem

Multi-strategy fetcher plus a dead-letter-queue for manual resurrection when a fetch fails. JD ingestion is not a happy-path assumption.

## D7 — Dual-use data safety: structural, not disciplinary

Real personal data and synthetic fixtures both flow through this tool. Safety is enforced by construction, not by user discipline. Seven concrete mitigations:

- **M1** Two-instance DB separation: `data/synthetic.db` tracked, `data/real.db` gitignored, mode selected by env flag, application refuses to cross modes (stamped mode marker).
- **M2** Belt-and-suspenders `.gitignore` with explicit real-data patterns (`data/*.db`, `!data/synthetic.db`, `*.private.*`, `profile.private.yaml`, `contacts.private.*`, `.env*`, `!.env.example`).
- **M3** Pre-commit hook scanning staged files for real-data name/email/phone patterns and a local danger list.
- **M4** Log-line hygiene: application code logs by ID, never by content.
- **M5** Prompt sanitizer: cross-cutting choke point on every LLM call. Redacts contact names to role tokens, refuses entries flagged `contains_personal`, wraps output in an authenticated payload so downstream code cannot fake having been sanitized.
- **M6** Recording/demo workflow documented; UI shows a persistent "SYNTHETIC MODE" banner.
- **M7** `profile.example.yaml` (tracked) vs. `profile.private.yaml` (gitignored) template pattern.

The migration also applies to personal dogfooding: drafter is draft-review only (no send); migrations tested on synthetic first; LLM budget caps enforced via the A5 instrumentation.

## D8 — Hard line on personal identity in LLM traffic

No personal notes about identifiable individuals go to any LLM, by name, ever. Applies in synthetic and real mode. Enforced structurally by the M5 sanitizer.

The portfolio narrative version: *"I built a management-adjacent tool that structurally cannot send identifiable person information to a third-party LLM."*

## D9 — LLM stages are SPLIT (extract → score), scorer sees raw JD too

JD extraction and fit scoring are separate LLM calls, not one combined call.

Reasons ranked:

1. **Eval discipline is the flagship signal.** Split enables extraction evals to check facts (level, comp band, stack) and scoring evals to check judgment (band placement, rationale quality) independently. When scoring regresses, we know it wasn't extraction drift.
2. **Intermediate output has independent product value.** The dashboard shows structured extracted fields regardless of scoring.
3. **Cost/quality boundary is real.** Extraction is Haiku territory; scoring is Sonnet territory. Compounded cost at 100+ applications matters.
4. **Caching / regeneration.** Extraction output is stable per JD; scoring output changes when profile changes. Split lets extraction be cached cheaply.

**Refinement:** scorer sees both the extracted structured JD and the raw JD text. Extraction inevitably loses nuance ("we move fast" vs. "we invest in engineering foundations") that matters for fit judgment.

**Alternative considered:** combine into one call. Rejected because criteria 1-4 dominate for this project — it is a case study in eval-driven agent design.

## D10 — Drafter: routing-first, routine-only composition

Two-step architecture — not specialist orchestration theater, but a real product decision about when automation is inappropriate:

- **Step 1 (Haiku, cheap):** router classifies the situation as `routine(intent)` or `non_routine(reason, considerations)`.
- **Step 2A (Sonnet, only when routine):** single-call composition with in-prompt style samples. No specialist split — voice/intent/context are inherently holistic.
- **Step 2B (no LLM, when non-routine):** structured briefing card assembled from the router's output. Surfaces the reason, suggested considerations, and an explicit "handle manually" flag. No prose draft.

Routine examples: post-interview thank-you, cadence nudge on stale application, logistics confirmation, standard recruiter acknowledgment.

Non-routine examples: rejection responses, compensation/negotiation, first outreach to warm contacts, multi-thread ambiguity, anything touching entries flagged with personal notes (per D8), any low-router-confidence case.

**Why this earns its slot:** knowing when *not* to automate is more sophisticated than knowing when to. Fits the ethics-adjacent story arc of the broader portfolio.

**Alternatives considered:**
- Orchestrator + specialist composers — rejected as multi-agent theater; composition is holistic and would fragment awkwardly.
- Single-call drafter always drafts — rejected because always-draft turns the tool into a "click to send" hazard for exactly the situations where human judgment is highest-value.
