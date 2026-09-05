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

**The ledger records the call, not the outcome.** The instrumented function is the billed unit and nothing more: the response is recorded before it is parsed, so a model reply that fails to parse still leaves a row with its real token count and cost. Parse failures cluster during prompt iteration, which is exactly when the cost figures are being read, and a ledger that silently omits the calls that went wrong reports a number that is too good (gate finding M2). For the same reason an unpriced model is rejected *before* the request is sent rather than defaulted to a known rate while pricing the response — a wrong figure in a cost-transparency artifact is worse than a refusal (gate finding M4).

**Scope, stated precisely.** Every call made through a CLI command is instrumented — `ingest` and `resolve-dlq` under the `extraction` feature, `eval jd_extraction` under `extraction_eval`. Eval traffic is labelled separately so prompt iteration is visible in `jscc costs` without inflating the per-application cost figure. The library API also permits a call with no database connection, for tests and embedded callers; that path cannot record, because there is no ledger to record to. Until the B5 hardening slice the eval command used that path, which left prompt iteration — the most token-hungry phase of the project — as the one phase with no cost record (gate finding M1).

## D6 — JD fetching is a real product problem

Multi-strategy fetcher plus a dead-letter-queue for manual resurrection when a fetch fails. JD ingestion is not a happy-path assumption.

**The fetcher is also a security boundary, not just a reliability one.** It takes a URL and forwards whatever comes back to a third-party model, so an unguarded `requests.get` is a server-side request forgery primitive with an LLM attached. Requests leave through one guarded path (`_get_guarded`): an http(s) scheme allowlist, rejection of any host that resolves to a non-public address, the same check re-run on every redirect hop rather than only on the URL the user typed, a redirect ceiling, and a streamed 5 MB body cap. Practical risk on a personal CLI is low — the user types the URL — but this repo's claim is structural safety, so the guarantee is enforced in code rather than assumed from the usage pattern (gate finding M5). Known residual: the opt-in Playwright fallback receives an already-checked URL, but the browser then follows its own redirects outside these guards.

## D7 — Dual-use data safety: structural, not disciplinary

Real personal data and synthetic fixtures both flow through this tool. Safety is enforced by construction, not by user discipline. Seven mitigations — five built, two marked `(planned)` below, because a safety list that quietly includes unbuilt items is the kind of overstatement D8 exists to avoid:

- **M1** Two-instance DB separation: `data/synthetic.db` tracked, `data/real.db` gitignored, mode selected by env flag, application refuses to cross modes (stamped mode marker).
- **M2** Belt-and-suspenders `.gitignore` with explicit real-data patterns (`data/*.db`, `!data/synthetic.db`, `*.private.*`, `profile.private.yaml`, `contacts.private.*`, `.env*`, `!.env.example`). The default data directory is anchored to the package, so a real DB lands inside the tree these patterns protect rather than wherever the process was started (rerun-gate M-3).
- **M3** Pre-commit hook scanning staged files for real-data email/phone patterns and the danger lists. (There is no name *pattern* — names are covered by danger-list literals, and by the role-token map on the M5 side. Stated precisely because the rest of D7/D8 is careful about this and this line wasn't.)
- **M4** Log-line hygiene: application code logs by ID, never by content.
- **M5** Prompt sanitizer: cross-cutting choke point on every LLM call. Redacts email addresses, phone-shaped digit runs, danger-list literals, and known contact names (to role tokens); refuses entries flagged `contains_personal`; wraps output in an authenticated payload so downstream code cannot fake having been sanitized. Redaction is unconditional and runs before the payload is authenticated, so it does not depend on the caller setting the flag correctly. The pattern definitions are shared with M3 (`jscc/personal_data.py`), so the two egress points cannot drift on what counts as personal data. **Both the patterns and the danger-list location are anchored to the installed package** (`jscc/paths.py`), not to the process's working directory, and the scanner reads the same two lists the sanitizer does. Rerun-gate findings H-1 and its follow-on: the lists were CWD-relative, so the sanitizer silently loaded nothing outside the repo root while the scanner — which always runs from the root — kept working; and the scanner read only the tracked scaffold, so a term added to the local list blocked LLM traffic but not commits. Sharing the regexes is not enough on its own: the two points have to agree on *which files* define the terms and *where those files are*, or the same drift returns by another route.
- **M6** Recording/demo workflow documented. Mode is stamped in the DB and printed on every `report` (`[mode: synthetic]`), so the active mode is always visible at the point of use. The persistent "SYNTHETIC MODE" banner is **(planned)** — there is no UI yet to put one in.
- **M7** `profile.example.yaml` (tracked) vs. `profile.private.yaml` (gitignored) template pattern.

The migration also applies to personal dogfooding: the drafter will be draft-review only (no send); migrations tested on synthetic first. **LLM budget caps are (planned)** — the A5 instrumentation meters every call and `jscc costs` reports the total, but nothing yet refuses a call for exceeding a cap. Metering is the prerequisite for a cap, not the cap.

## D8 — Hard line on personal identity in LLM traffic

No personal notes about identifiable individuals go to any LLM, by name, ever. Applies in synthetic and real mode. Enforced structurally by the M5 sanitizer, which redacts unconditionally rather than relying on any caller-set flag.

**Scope of the guarantee, stated precisely.** M5 removes structured identifiers (email addresses, phone-shaped digit runs), every literal on the danger list, and any contact name supplied to it by a caller that holds contact records. It does *not* do free-text named-entity recognition: an unfamiliar person's name sitting in pasted prose, with nothing else to key on, is not detected. Closing that gap needs NER, not regex. The boundary is documented here rather than papered over, because an overstated safety claim is worse than a narrow one — the claim is the dangerous part.

The portfolio narrative version: *"I built a management-adjacent tool where the LLM egress path structurally cannot carry contact identifiers — enforced by a choke point that redacts before it authenticates, sharing one pattern definition with the pre-commit scanner, so neither egress point can drift from the other."*

Local storage is deliberately **not** redacted. D7 governs egress — what leaves for git or for a third-party model. The user's own SQLite record keeps what the user pasted.

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
