# ADR 007: dashboard web stack — FastAPI + Jinja2 + HTMX

**Status:** Accepted (2026-09-21, ahead of Slice E1)

## Context

Phase E needs a local dashboard: read-only pipeline/funnel/stale views (E2a),
plus one write path — resolving a DLQ entry via the paste flow (E2b). No BYOK
or public hosting in v1 (D4), so the audience is a reviewer or Jess running it
locally, not the general public. JSCC's signature signal is agent design +
eval discipline (D9/D10), not frontend architecture — the stack choice should
not become effort spent where the portfolio doesn't need it, and should not
blow past a slice's 4hr cap on toolchain setup alone.

## Decision

**FastAPI + Jinja2 server-rendered templates, with HTMX for the interactive
pieces** (DLQ resolve, any filtering/sorting on E2a's views). No SPA, no
separate JS build step, no `node_modules`. The whole stack stays inside
JSCC's existing Python toolchain.

## Alternatives considered

- **FastAPI + Jinja2, no HTMX (plain server-rendered HTML).** Simplest
  possible option, but the DLQ resolve action and any list filtering would
  need either full-page reloads or hand-rolled JS. HTMX buys partial-page
  interactivity for near-zero added complexity over this baseline. Kept as
  the fallback if HTMX itself turns out to be scope creep mid-slice.
- **FastAPI backend + React/Vite SPA.** Most polished, most "modern web app"
  look for the video walkthrough. Rejected: a real toolchain (npm, a build
  step, two dev servers) risks the E1 4hr cap and drags E2a/E2b with it, for
  a signal (frontend polish) this project isn't signature on. Revisit only
  if a specific interview loop explicitly probes frontend depth post-v1.
- **Streamlit / Gradio.** Fastest to stand up, but reads as a quick-demo tool
  rather than a shipped product, and fights the custom DLQ resolve/paste
  interaction. Weaker systems-judgment (#8) signal than a real app shell.

## Consequences

- Positive: E1 stays a scaffold-and-verify slice, not a toolchain-setup
  slice — no new language, no new package manager, no new CI surface.
- Positive: the same "rule-based over LLM/fancy tooling unless it earns its
  slot" judgment that shaped D9/D10 now shows up in the frontend decision
  too — consistent engineering taste across the repo, which is itself part
  of the narrative signal (#10).
- Cost: HTMX is a smaller bet than React from a "modern stack" optics
  standpoint. Accepted — the target audience (EM/SWE/DE/MLE reviewers) reads
  engineering judgment, not framework fashion, and #2 (agent design) and #3
  (eval discipline) remain the project's signature signals, not the
  dashboard.
- Revisit if: E2a/E2b interactivity needs outgrow what HTMX comfortably
  expresses (unlikely at this project's scope), or a specific interview loop
  asks for frontend-architecture depth this project doesn't currently show.

## Related

- Parent plan discussion queue #4 (web UI stack), resolved by this ADR.
- D9/D10 (LLM-vs-code and routing-vs-composition splits) establish the same
  "earn its slot, don't default to the fancier option" judgment pattern this
  ADR applies to the frontend.
