# ADR 007: dashboard web stack — FastAPI + Jinja2 (HTMX not adopted)

**Status:** Accepted (2026-09-21, ahead of Slice E1). Amended 2026-09-24: HTMX was never used and is removed; see the addendum at the end.

## Context

Phase E needs a local dashboard: read-only pipeline/funnel/stale views (E2a),
plus one write path — resolving a DLQ entry via the paste flow (E2b). No BYOK
or public hosting in v1 (D4), so the audience is the one person running it
locally, or a reviewer running it against the synthetic fixture. The project's
subject is agent design and eval discipline (D9/D10), not frontend
architecture, so the stack should cost as little toolchain as possible and
should not use up a slice's time budget on setup alone.

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
- **FastAPI backend + React/Vite SPA.** Most capable for rich client-side
  interaction. Rejected: a real toolchain (npm, a build step, two dev servers)
  adds a second language and a second CI surface to a tool whose views are
  tables and one form. Revisit if a view needs client-side state that server
  rendering cannot serve.
- **Streamlit / Gradio.** Fastest to stand up, but it fights the custom DLQ
  resolve/paste interaction and gives less control over what the server
  exposes (routes, headers, request checks), which matters for a tool that
  reads real personal data.

## Consequences

- Positive: E1 stays a scaffold-and-verify slice, not a toolchain-setup
  slice — no new language, no new package manager, no new CI surface.
- Positive: the same "rule-based over LLM/fancy tooling unless it earns its
  slot" judgment that shaped D9/D10 applies to the frontend decision too.
- Cost: no client-side interactivity beyond links and forms. Accepted: the
  views are read-mostly tables.
- Revisit if: a view needs partial-page updates or client-side state. The
  addendum below says how to add a script safely.

## Related

- The web-stack question deferred earlier in the build, resolved by this ADR.
- D9/D10 (LLM-vs-code and routing-vs-composition splits) establish the same
  "earn its slot, don't default to the fancier option" judgment pattern this
  ADR applies to the frontend.

## Addendum, 2026-09-24 (Phase E gate): HTMX removed, plain HTML kept

The decision above named HTMX for the DLQ resolve and any list filtering. As
shipped, neither needed it: the resolve action is an ordinary form post that
renders a result page, and no view filters or sorts in place. No template ever
carried an `hx-` attribute. The base template did load the library from a CDN
(`unpkg.com`, no integrity hash) on every page, which made unpinned third-party
code a dependency of a page that shows contacts and interaction notes, for no
behavior the pages used.

**Now:** the dashboard is FastAPI + Jinja2 server-rendered HTML with no script
and no third-party origin, which is the "no HTMX" alternative listed above. A
test fails if any template gains a `<script>` or an absolute URL.

**Why not keep it "for later":** a dependency loaded from a third party is a
standing cost (any compromise of it reads and writes real-mode pages); an unused
one has no offsetting benefit. If a view later needs partial updates, vendor a
pinned copy into the repo or load it with a subresource-integrity hash, and
record that here.

The alternatives and consequences above were reworded on the same date to drop
reasoning about how a stack looks in a demo or to a reviewer. The stack is
chosen because it is the smallest one that serves a local, single-user,
mostly read-only tool, and that is the only reason it needs.
