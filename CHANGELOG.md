# Changelog

Slice-by-slice arc, newest first. Phase E, the phase now closing, keeps its
reasoning in full. Phases A to D are summarized: the shape of the build and the
lessons worth keeping, with per-slice detail in git history. Review findings are
recorded here rather than in code comments.

Standing practice: at each phase gate, the phase before the one that just closed
is folded into a summary. Phase D was folded at the Phase E gate; Phase E will be
folded at the Phase F gate.

Slice names (A1, B2b, C2a, D4c...) are build steps. They are unrelated to the
design principles D1 to D10 in `docs/design-principles.md`.

## Phase E — dashboard (E1–E2b shipped 2026-09-21; gate in progress)

The dashboard is a local FastAPI + Jinja2 app over the same storage layer as the CLI. The Phase E gate ran a backlog sweep, a recapture of the extraction and scoring prompts, two cold lenses, and a hardening slice; its entries come first, newest first, followed by the three build slices. The gate is not closed: the remaining fixes are tracked in the gate notes and will be summarized here when they land.

### Phase E gate, fixes B: the lightweight findings

- **Published results pin which cases miss (L1-12).** The pinning test compared pass counts, so one pass and one fail swapping places stayed green, and nothing tied the hostile-posting cases T5 rests on. It now pins the exact set of misses per suite and requires every hostile case to pass in the suites that have them.
- **A moment of clock skew is not bad data (L1-14).** A reference timestamp a second ahead of the reading clock raised, and one such row returned a 500 for the whole dashboard index and a traceback from `jscc report`. Skew up to five minutes counts as age zero; anything further ahead still raises, now as a named error (a 500 page that says which row on the dashboard, exit 1 with a message on the CLI).
- **Opening a current database takes no write (L1-13).** Every dashboard request re-ran the schema DDL and re-stamped the version, and would have lowered a newer database's stamp. A database already at this version is only checked for missing columns, and the stamp only moves upward.
- **The pre-commit scanner reads UTF-16 and legacy-encoded text (L1-16).** Windows PowerShell 5.1's `>` writes UTF-16, which failed the UTF-8 decode and was skipped as binary, so a redirected text dump committed unscanned. A UTF-16 file (by byte-order mark) and a single-byte-encoded text file are now scanned; a file with NUL bytes that is not UTF-16 is still treated as binary.
- **Smaller.** The comment on the extraction API-error path now states that a pasted text is still lost (L1-11), the dashboard test that flagged only the overdue application checks the alerts section instead of the whole page, and `docs/gate-reviews.md` no longer says the recordings are not a CI gate or lists a closed item as open (W7).

Tests: 704 to 718. Each fix was checked by removing it and confirming a test failed.

### Phase E gate, fixes A: walkthrough highs and the findings that share their files

The gate's outside-reviewer lens found the README, the ADRs and this file describing a slightly better system than the one that exists, and three earlier "fixed" items had regressed.

- **Egress test made structural (L1-5).** The scan matched only a literal `.complete(` call. An alias (`send = client.complete`), `getattr(client, "complete")` and an import of `stage_call._raw_call` all skipped the sanitizer and left it green, confirmed on a scratch copy. It now flags any reference to the client's `complete` or to the unsanitized helpers outside `stage_call`; six bypass shapes have tests. It cannot stop deliberately obfuscated code, and T8 says so.
- **DLQ resolve (L1-17, L1-10).** A posting that is already an Application, whether ingested later or created by a resolve that crashed before marking the entry, is reported as a duplicate and linked instead of created twice. The comment claiming `FailureMode.other` is never produced was wrong: `ingest` writes it for an extraction API error, and `manual` is the correct fetch status when the text arrives by paste.
- **Real mode refuses the placeholder extractor.** With no key, extraction falls back to a stub. `ingest` and the resolve paths in real mode saved a made-up application beside real ones; they now stop first.
- **Public docs (W1, W15).** The private-workspace path and references to plans the repo does not contain are gone from this file, the evals README and ADR-007, and first person replaces third person. `tests/test_public_docs.py` fails if any return; the Phase D scrub had no check and did not last.
- **Eval claims (W4, W5, W6, W8, W13).** The README's opening paragraph still cited fit scoring as 84% (21/25); it now gives the path (84%, 64%, then 27/28 twice, tuned). "Validated" now follows ADR-006's two-round rule, so extraction's current prompt is one round and not validated, and "band" means rounds with the prompt held fixed. The evals README gained a threats-to-validity section (chat, not API; provenance from my account; no live path has run; tuned, not held out) and lost its errors. The README test now rejects a superseded figure that is not beside its replacement, and requires the extraction figure to say it is not held out.
- **ADR-007 (W3).** Reworded in engineering terms; the addendum records that HTMX was never used.
- **README and this file (W9, W10, W11).** Status is now one table plus one section per topic instead of four overlapping ones; ADR, threat and test counts corrected. This file went from 696 to about 350 lines: Phase D is folded into a summary, and the Phase E entries are newest first.

Tests: 689 to 704.

### Phase E gate, hardening slice 1: dashboard and fetcher (adversarial findings L1-1, L1-2, L1-3, L1-6, L1-7, L1-9)

Both cold lenses ran at the Phase E gate. This slice fixes the adversarial lens's High findings and the dashboard and fetcher findings that share their files; the rest of the gate's findings are tracked in the gate doc.

- **Dashboard request guards (L1-1).** Binding to 127.0.0.1 keeps other machines out, not a web page in the user's own browser: DNS rebinding points an attacker's domain at the loopback address, and the browser then treats the dashboard as that page's own site. A probe read `/dlq` with a foreign `Host` and created an Application with a foreign `Origin`. Now every request's `Host` must be on an allowlist (loopback names plus the address `serve` binds), and a state-changing request with a foreign or `null` `Origin`, or a cross-site `Sec-Fetch-Site`, is refused. Threat model T13.
- **DLQ resolve race (L1-2, contradicts the earlier idempotency fix).** The guard checked "still unresolved" before a seconds-long model call and wrote unconditionally after it, so a double-click made two Applications. Resolves are now serialized per entry, and the final write is a compare-and-set that a second process can lose, in which case its Application is deleted. The form's button disables on submit.
- **IDN hosts and the DNS pin (L1-3, contradicts the DNS-rebinding fix).** `urllib3` resolves an internationalized host in punycode, so a pin keyed on the URL's spelling never matched and the connection was resolved unpinned. The URL check and the pin now compare through one normalization (lowercase, no trailing dot, IDNA), and a host that cannot be encoded is rejected. Overlapping pins are serialized, since the patched resolver is process-global (L1-15).
- **Concurrent page loads (L1-6).** The per-request connection was pinned to its creating thread while the framework runs setup and teardown on other workers; 150 of 200 concurrent GETs returned 500. The dashboard connection now opts out of the same-thread check.
- **No script, no third-party origin (L1-7).** The base template loaded HTMX from a CDN with no integrity hash, and nothing used it. Removed; ADR-007 has an addendum, and a test fails if a template gains a `<script>` or an absolute URL.
- **Non-http source URLs are not linked (L1-9).** A stored `javascript:` URL was rendered as a link.

Tests: 654 to 689. Each fix was checked by removing it and confirming a test failed.

### Phase E gate, extraction prompt: skills rule tightened after a failed 36-case capture (recaptured: 32/36, 89%)

The first capture of the 36-case jd_extraction suite scored 20/36 (56%) against the 80% bar, where the last 33-case round had scored 27/33 (82%). Diagnostic chats cleared the model choice, chat memory and the one new prompt paragraph: Haiku was over-including skills the rules exclude (a title's restated experience such as "SRE", a tech stack copied from a stack paragraph) and paraphrasing requirement wording. `EXTRACTION_SYSTEM_PROMPT` now takes every skill from the requirement's own words, never from responsibilities, title or stack, lets a trailing "preferred" cover its whole sentence, excludes track-record statements and context-only domains (with carve-outs so "managing managers" and "production deployment" still extract), and makes a figureless "competitive compensation" a null comp band. After round 2 (27/36, 75%) the misses were debugged in real chats, and the prompt gained title-only titles, years of experience never raising the level, and six worked examples on invented postings.

Round 3 scored 32/36 (89%) on the frozen prompt; misses 06, 26, 27, 31. Three fixture expectations were widened before the recapture (see "Expected skills accept the posting's own wording" in `evals/README.md`); a smarter synonym grader stays deferred until the planned phases are complete. Not a held-out rate: the prompt was debugged against these same 36 fixtures, and round 3 is one round of its final prompt. Proxy runs (26/36 up to 30/36) were advisory only. Round-by-round detail is in `evals/README.md`.

### Phase E gate, model provenance: scoring and composition recordings were Sonnet 5, not 4.5

My manual captures run in Claude.ai chat, whose default Sonnet has been Sonnet 5 since 2026-06-30. I used that default for every Sonnet capture (composition 2026-09-20, both scoring rounds), while the prompts printed `claude-sonnet-4-5-20250929` and the recordings were keyed to it, so the docs named a model that did not produce them. This rests on my account and the launch date; the chats cannot be re-inspected. `SCORING_MODEL` is now `claude-sonnet-5` (`COMPOSITION_MODEL` aliases it), priced at $2 / $10 per million tokens (the announced September increase was cancelled, per the pricing page, checked 2026-09-24). The 56 scoring and composition recordings were re-keyed by recomputing each key with the new model; replies are untouched and replays are identical (composition 24/28, scoring 27/28). Extraction is Haiku 4.5 and unaffected. Any future recapture should confirm the model in the chat's picker.

### Phase E gate, scoring prompt: numeric bands and an adjacent-title anchor after a failed 28-case capture (recaptured, 27/28 in two rounds)

The first capture of the 28-case fit_scoring suite scored 18/28 (64%) against the 80% bar; the hostile cases passed 3/3. Of ten misses, four were rubric gaps and six were fixture ranges that contradicted the prompt. `SCORING_SYSTEM_PROMPT` now states the bands it had left implicit (a comp shortfall caps at about 45 to 70, a missing must-have at about 15 to 50), anchors an adjacent title with everything else met in the low 70s, counts a comp band whose midpoint clears the minimum as within range, and treats a title using the profile's own target words as on target. Nine fixture ranges were corrected before the recapture, justified by consistency with the prompt rather than by what the model returned (table in `evals/README.md`). The same change dropped `display_name` and `style_samples` from the scoring payload (gate finding G4).

Two rounds of 28 on the revised prompt replayed 27/28 (96%) and 27/28, the same case (24, a Tech Lead) missing both times. Not a held-out rate: the prompt and nine ranges were tuned after the 64% capture. The recording holds round 2; round 1 is in commit `734ba48`.

### Phase E gate, backlog sweep: fetcher ignores proxy environment variables

The Phase D gate recorded a new residual on the DNS-rebinding fix and never dispositioned it: with `HTTPS_PROXY` or `ALL_PROXY` set, `requests` hands the target hostname to the proxy, and the proxy's own lookup is one the pin cannot see. A rebinding answer at that point reaches whatever the proxy can reach. Every fetch now goes through a session with `trust_env = False`, so the request always connects direct to the address that was checked. A machine that can only reach the web through a proxy now gets `blocked` fetches, which land in the DLQ with the usual manual-paste remedy. The new test fails with that line removed.

### E2b: application detail, DLQ views, and the one write path in Phase E

Pipeline and stale-alert rows on the dashboard now link to `/applications/{id}`:
title, company, stage, fit score and rationale, the full extracted JD, contacts,
and the interaction timeline, all off the same storage functions the CLI already
had (`get_application`, `list_contacts`, `list_interactions`) — no new read path.

The DLQ half needed an actual write, and D6's `resolve-dlq` already carried real
behavior worth not duplicating: the idempotency guard (gate finding M-1/M-9) and
the `dlq_*` fetch-status mapping (gate finding M-6). Rather than reimplement
either in the web layer, `_extract_and_create_application` and its small helpers
moved out of `jscc/cli/ingest.py` into a new `jscc/ingest_logic.py`, and the
resolve logic itself moved into a new `jscc/dlq.py` as `resolve_dlq_entry_via_paste`
— a typed result (`DLQResolveOutcome`) instead of echo calls and `sys.exit`, so
each caller renders it in its own idiom. `resolve-dlq` is now a thin translation
of that result into click's exit-code contract; all 85 existing CLI tests passed
unchanged after the refactor, which is the point — the behavior didn't move, only
where it lives. The dashboard's `/dlq/{id}/resolve` form calls the identical
function, so the CLI and the browser cannot drift on how a DLQ entry gets resolved.

Manually verified end to end against a freshly seeded synthetic DB: loaded the
resolve form for a real `blocked` DLQ entry, posted a pasted JD through the actual
HTTP form (not a test client), got back a created-application link, and confirmed
`jscc dlq list` immediately stopped showing that entry as unresolved — the CLI and
the web session were reading the same state. +19 tests across `tests/test_dlq.py`
(new, direct coverage of `resolve_dlq_entry_via_paste`) and `tests/test_web.py`
(643 total).

### E2a: pipeline, funnel, and stale-alert views

The index route now renders what `jscc report` already prints as text: a funnel
table (every configured stage, including zero-count ones), a pipeline table
grouping applications under their stage, and the stale-alert list. All three read
`report.py`'s pure functions — `funnel_counts`, `detect_stale`, and a new
`group_by_stage` added alongside them — so the CLI and the dashboard render the
same underlying computation and cannot silently disagree on what counts as stale.

A `?now=` query parameter mirrors the CLI's `--now`: same ISO-8601-with-timezone
format, same 400-on-bad-input framing as `report`'s UsageError, so a pinned seed's
stale block is reproducible from a browser the same way it already was from a
shell. Manually verified against the seeded synthetic fixture and `--now
2026-08-28T12:00:00+00:00`: funnel counts, pipeline listing, and all 13 stale
alerts matched `jscc report`'s output line for line. +7 tests (629 total).
Application detail + DLQ resolve (E2b) are next.

### E1: web scaffold (ADR-007)

Resolved the deferred web-stack discussion (the deferred web-stack question): FastAPI + Jinja2 +
HTMX, no SPA, no separate JS build step — the dashboard stays in JSCC's existing
Python toolchain, matching the "earn its slot over the fancier default" judgment
already applied to the LLM-stage splits (D9/D10). ADR-007 records the alternatives
(React/Vite, plain server-rendered HTML, Streamlit/Gradio) and why they lost.

`jscc/web/` holds a `create_app(data_dir, config_dir)` factory and Jinja2 templates;
`jscc serve` boots it (127.0.0.1 by default, local-only per D4 — no BYOK). The index
route opens the active mode's DB through the same `open_for_mode` contract the CLI
uses, shows the application count, and renders the SYNTHETIC MODE banner (D7 M6) a
slice early, since it was cheap to add once the template existed. +3 tests (622 total).
Pipeline/funnel/stale views (E2a) and application detail + DLQ resolve (E2b) are next.

HTMX, named here, was never used and was removed at the Phase E gate; see the addendum in ADR-007.

## Phase D — follow-up drafter (D1–D5, closed 2026-09-20; Phase D gate 2026-09-20)

Routing first, then composition for routine situations only, and a briefing card for everything else (design principle D10). Both prompts were checked against real model output captured by hand. Per-slice detail is in git history.

**The build**

| | |
|---|---|
| D1 | Routing eval suite, resized 12 → 20 → 26 cases: the first two passes for standard error before a capture, the third for the missing-information rule (a reply that must state an unrecorded detail goes to a person). |
| D2 | Routing prompt (Haiku) and call path. A routine answer must carry an intent and no reason, enforced at parse time. Five manual-capture rounds; round 4 failed the zero-false-routine gate on a case the proxy runs had passed. Round 5 scored 26/26, on the same cases the prompt was tuned against, so it is not a measured rate. |
| D3 | Composition eval suite, resized 8 → 25 cases before any capture, then 28 with three escalation cases. |
| D4 | Composition prompt (Sonnet), a deterministic grader with no judge of tone (word range, invented numbers and names, verbatim style-sample reuse, a stock-phrase advisory), and a `needs_input` escape so the composer declines instead of inventing a fact. One manual round: 24/28 (86%), all three decline cases correct. Parsing became tolerant of a prose or code-fence wrapper across routing, extraction and scoring. |
| D5 | Briefing renderer and the `followup` orchestrator: a draft for routine, a briefing card for everything else, and a card for any malformed or unknown router output. |

**What the Phase D gate changed**

- **One path to the model.** Extraction, scoring, routing and composition each carried a copy of sanitize, verify, meter, call. They now share `jscc/stage_call.py`, the only module that calls the client. The egress test had stopped scanning the CLI when it became a package that morning; it now walks subpackages.
- **Redaction that matches its documentation.** Stored contact names were documented as redacted and no caller supplied them; routing and composition now load them. Prompts were serialized to ASCII escapes before redaction, so an accented danger-list name went out; string fields are now redacted first.
- **The drafter no longer sees posting text**, the one input a stranger controls, aimed at the component with a zero-tolerance gate.
- **Gates that can fail.** The false-routine gate had no test that failed when the gate was removed; it and the composer's must-ask gate now do. A hedged "routine" answer is malformed, published eval numbers are pinned to the recordings by a test, `--record` refuses to record a stub over hand-captured replies, and `jscc costs` shows failed calls.
- **Older databases** are migrated by column presence before the version is stamped; the earlier schema bump had stamped them without migrating.
- **Terminal output** strips control characters through one helper.
- **Prose hygiene:** the README contradicted itself on composition's status, about sixty review-finding IDs had crept back into code, public docs linked into a private workspace, and ADR-006 records the eval decisions that had none. `cli.py` (1,422 lines) was split into a `jscc/cli/` package.

**Lessons worth keeping**

- A proxy run gates whether a manual capture is worth running. It never replaces one (routing round 4).
- A test that cannot fail is not a gate. Each zero-tolerance gate now has a test that fails when the gate is removed.
- A guarantee enforced by an inspection test needs the inspection to cover the structure it guards, which the egress scan did not when the package layout changed.
- The capture scripts moved from a session scratchpad into `scripts/capture_tools.py`, and a checklist and a sizing rule (at least 25 cases, with n and its standard error stated) came out of the phase's three resizes.

## Phase C — fit scoring (C1–C3, closed 2026-09-12; Phase C → D gate 2026-09-12)

The second LLM stage: a scorer that sees both the extracted fields and the raw
posting (design principle D9), its eval suite, its first manual-capture round, and
the cost report that finally reads the ledger back. Per-slice detail is in git history.

**The build**

| | |
|---|---|
| C1 | Fit-scoring eval suite. Each case grades a score band, not an exact score, because a fit judgment has no single right answer. Rationale is checked for presence only. |
| C2a | Scoring prompt (Sonnet) and call path, behind `StubScoringClient`: there is no API key for this project. |
| Sizing | Suite resized 10 → 25 cases before any capture effort was spent, from the binomial standard error at the 80% bar. Extraction had learned the same lesson mid-round in Phase B. |
| C2b | `eval --manual`: prints each prompt to paste into a chat and records the pasted reply, keyed exactly as a live recording would be. Round 1: 21/25 (84%), above the bar. Three misses sat just outside a band on comp/level judgment. The fourth (case-14) is a real prompt finding: an adjacent higher title reads as "far outside" the target role. Deferred to the next scoring-prompt change, so one re-capture covers it. |
| C3 | `jscc costs`: per-feature cost, p50/p95 latency, and a check that flags any recorded cost that no longer matches its model's published rate (the Phase B pricing error, found by hand, is the shape it catches). |

**What the Phase C → D gate changed**

- **An earlier "accepted residual" was wrong.** The fetcher checked one DNS resolution and then let the HTTP library resolve again to connect, so a rebinding domain could pass the check and connect to a private or cloud-metadata address. An earlier gate had rated this "reasoned, not exploited" and documented it. This gate found the concrete path. Every request, including each redirect hop, is now pinned to the addresses already validated. The lesson became a line in the gate method: re-derive the severity of a carried-forward residual, not just its presence.
- **A model score of `NaN` or `9001` would have been stored.** `FitResult.score` is now bounded to 0–100, which also rejects non-finite values.
- **A call that failed mid-request left no ledger row**, although the provider may already have billed it. It now writes a marked row. The schema bump that added the column turned out, at the Phase D gate, to stamp older databases without migrating them. Fixed there.
- **Kept, deliberately:** trimming unused profile fields from the scoring payload would invalidate every recording, so it waits for the next scoring-prompt change. The walkthrough flagged CHANGELOG length for the third time; Phase B was compacted the same day.

## Phase B — ingestion + extraction (A5, B1–B14, closed 2026-09-04; Phase B → C gate closed 2026-09-12)

Fourteen slices (plus A5, LLM instrumentation deferred from Phase A) building
the first two AI-native stages: the extraction eval suite, the extraction
prompt, the URL fetcher with DLQ, the paste-only escape hatch, and three full
rounds of adversarial review plus outside-reviewer walkthrough — the second of
which ran cold (each reviewer barred from the prior gate doc until it had
formed its own findings) and immediately produced a CONTRADICTS against a fix
from the slice under review. 348 tests at close, plus a lint/format gate added
just before Phase C started. Per-slice detail is in git history; what follows
is the shape and the decisions worth keeping.

**The build**

| | |
|---|---|
| A5 | LLM call instrumentation, deferred from Phase A. Slipped past all three Phase A gate rounds because every round reviewed shipped code, not missing slices — no round had reason to look for what wasn't there. `@instrumented(feature)` decorator, `llm_calls` table (schema v3), `jscc costs`. |
| B1 | JD extraction eval suite. `ExtractedJD` contract, 15 hand-authored JDs, hand-rolled harness (structural fields exact, skills by set equality, prose non-empty only). DoD met: runs, reports 0/15 — no prompt yet. |
| B2 | Extraction prompt v1 + client plumbing, landed behind `StubExtractionClient` since no `ANTHROPIC_API_KEY` exists for this project — scoped honestly as wired, not validated. `EXTRACTION_MODEL`'s digit run trips the phone-pattern scanner; this false-positive class recurred five times across the project (danger-list example, model id, a comment quoting the model id, `uv.lock` hashes, IP literals in tests) before the convention — split the literal or describe the shape, never loosen the regex — stuck. |
| B3a | Baseline fetcher + DLQ core, per D6. `requests` + `readability-lxml`, every non-2xx/exception classified into a `FailureMode`, never raises. `ingest --url`, `dlq list`, `resolve-dlq --paste-text`. |
| B3b | Playwright fallback (opt-in, ~200MB dep, confirmed explicitly) + a real-URL smoke test against 5 live postings chosen via browser, not guessed — an SPA site and an authwalled search both recovered with the flag on; a straight bot-block stayed blocked either way, correctly. |
| B4 | JD paste-only path — the D6 escape hatch for a site the fetcher can't crack at all. Routes through the same helper as the URL path, so "same Application shape" is structural, not conventional. |
| B5–B9 | **Phase B → C gate hardening**, including a cold rerun that produced the project's first CONTRADICTS. Full detail below. |
| B10 | The prose pass — five README/docstring claims that described a system slightly better than the one that existed (see below). |
| B11 | The tracked synthetic fixture stopped being tracked (see below). |
| B12 | Corrected a 20%-under Haiku pricing error before any real spend. |
| B13 | The scanner and sanitizer both learned to catch an Anthropic API key shape, as its own category, not filed under "personal data." |
| B14 | The README's stated test count, fixed and then made structurally unable to drift again — the fourth time that figure had gone stale. |
| Backlog + lint gate | Closed the entire A2/A9/A10-era backlog (walkthrough #5–#7, three low-severity items, one field-whitelist gap) and added ruff (lint + format) ahead of Phase C, which itself surfaced 4 real bugs on first run. Full detail below. |

**What the gates actually changed** — the durable ones, several of which recur
as lessons the Phase C → D gate cited directly:

- **The sanitizer authenticated payloads it never redacted (C1, B5).** `_transform`
  had been an identity snapshot since Phase A, deferring real redaction to "when
  the first prompt is written." Phase B shipped a prompt and two production call
  sites without it — demonstrated concretely by one string that was *blocked from
  git* by the pre-commit scanner and *forwarded verbatim to the LLM* by the
  sanitizer, two mitigations named in the same design principle disagreeing
  completely about what counts as personal data. Fixed as one definition
  (`jscc/personal_data.py`), two enforcement points; redaction is unconditional
  and runs before the HMAC authenticator, so no caller can opt out and no
  verified payload can carry the original text. Scope stated honestly: structured
  identifiers and danger-list literals, not free-text NER.
- **The same drift recurred through path resolution, not duplicated regexes
  (H-1/M-3, B6).** The danger list and the data directory both resolved against
  the process's working directory rather than the package root, so running from
  anywhere but the repo root silently emptied the name list and put `real.db`
  outside its own `.gitignore` — one wrong `cd` disabled two mitigations at once,
  invisibly, since email/phone redaction kept working. Fixed with one
  `PACKAGE_ROOT` every module anchors to, per the same "one definition, two
  enforcement points" argument C1 had just made about the rules themselves — the
  lesson generalizes past the specific regex it was first learned on.
- **A test that cannot fail when the thing it covers is deleted is not covering
  it (H-4, M-4, B8).** The M5 fetch guards and the sanitizer redaction path both
  survived their own removal without a single test failing, because every guard
  test patched `requests.get`/`extract_jd` wholesale rather than observing the
  guard's actual behavior. Both fixed by verifying at a lower level (a spy on
  `HTTPAdapter.send`; a spy client checking the redacted text actually crosses)
  and confirmed by deletion, not by passing.
- **"Nothing that can fail after the money is spent belongs inside an
  instrumented function" (M2, B5/B9).** A billed call that failed to parse, or
  decoded incorrectly, or got its usage discarded (H-3) all shared one root
  cause: work capable of failing was running *inside* the ledger-writing wrapper,
  so the exception beat the write. The rule, not just the individual fixes, is
  what carried forward — B9's response-decoding fix and the eventual Phase C → D
  gate's G3 (a network fault losing the ledger row entirely) are both instances
  of the same principle applied to a new point of failure.
- **A transient failure has to produce a DLQ entry, not a crash (H-2, H-6,
  B6/B9).** Both an extraction parse failure and a transient Anthropic API error
  used to propagate straight out of the SDK call, crashing `ingest`/`resolve-dlq`
  with no DLQ row to retry from — violating D6's own contract ("produces an
  Application or a DLQEntry, never crashes") for exactly the failure modes a live
  key would actually hit.
- **A cold second-opinion review beats an anchored one (B6).** Rerunning both
  lenses with neither reviewer shown the prior gate's findings produced a
  CONTRADICTS against a fix from the very slice under review, and both lenses
  independently found the same top hole (the danger-list path bug above) —
  neither would likely have surfaced under the anchored method the first pass
  used. This is the method the Phase C → D gate also ran cold, and it produced
  its own CONTRADICTS there too.
- **The repo can describe a system slightly better than the one that exists,
  and a cold walkthrough reader notices immediately (B10, B14, lint-gate
  README fix).** The published sample output didn't reproduce under the words
  "Bit-reproducible"; the README's stated test count went stale four separate
  times; a self-contradicting Status paragraph got fixed at one location and
  stayed wrong at another. The durable fix each time was structural, not a
  promise to remember: `report --now` sharing one parser with `seed` so the
  sample can't drift, a test comparing the README's stated count against what
  the suite actually collected, and — eventually — the compaction this section
  is itself part of.

**Still open from Phase B: nothing.** Every finding across the three gate
passes (2026-09-01, the cold rerun at 2026-09-04, and a third pass the same
morning B2b closed) is closed, fixed, or documented as an accepted residual as
of 2026-09-12; the full review notes are private working files, and
[docs/gate-reviews.md](docs/gate-reviews.md) has the method and worked examples. The entire
carried-forward A2/A9/A10-era backlog (walkthrough #5 ADR-001 framing fixed,
#6 coverage badge killed, #7 CHANGELOG split killed the first time, then
replaced by compacting one phase behind — plus the `update_application`
field-whitelist gap and three low-severity sanitizer/report findings) closed
the same day, caught by the first backlog sweep (a standing step that opens every gate) rather than riding
through indefinitely. Ruff (lint + format) landed the same week, ahead of
Phase C, and surfaced 4 real bugs — a stale `__all__` export, three swallowed
exception chains, an unverified blind `pytest.raises(Exception)`, and a Python
version the codebase could already assume but hadn't used — on its first run.

---

## Phase A — foundations (A1–A10, closed 2026-08-29)

Ten slices building the non-LLM substrate: config, storage, a deterministic
synthetic fixture, staleness reporting, the dual-use safety architecture, and
three full rounds of adversarial review plus outside-reviewer walkthrough. 142
tests at close. Per-slice detail is in git history; what follows is the shape and
the decisions worth keeping.

**The build**

| | |
|---|---|
| A1 | Package scaffold, pydantic config models for `stages.yaml` / `profile.yaml`, `validate-config`. **ADR-001** (pydantic over jsonschema). |
| A2 | Storage: pydantic domain models, SQLite schema with FKs and cascade rules, CRUD with a field whitelist and auto-touched `updated_at`, `PRAGMA user_version` versioning. **ADR-002** (stdlib `sqlite3`), which also documents the single-writer limit and the naive-datetime contract. |
| A3 | Deterministic synthetic seed: 25 applications across every stage, 19 contacts, 40 interactions, 3 DLQ entries. Hardening pass made interaction chains chronologically coherent, wired HM contacts, and varied JD content by role type. |
| A4 | Staleness detector + funnel counts as pure functions over `list[Application]`; `jscc report`. Reference timestamp is `last_interaction_at` when set, else `created_at`. |
| A4.5a | **Environment isolation (D7 M1/M2/M7).** `Mode` enum via `JSCC_DATA`, one DB per mode, a marker stamped *inside* the DB so a cross-mode open raises rather than relying on path convention. `profile.private.yaml` preferred over the tracked example. **ADR-003.** |
| A4.5b | **Content controls (D7 M3/M5).** The pre-commit scanner and the sanitizer skeleton. **ADR-004.** |
| A6–A10 | Three gate rounds and their fixes (below). **ADR-005** documents sanitizer authenticity with six rejected alternatives. |

**What the gates actually changed** — these are the durable ones, and several
recur as lessons in Phase B:

- **Sanitizer authenticity (A6).** `sanitize_for_llm` returns a frozen
  `SanitizedPayload` carrying an HMAC over stable-JSON(data) + timestamp, keyed
  by a per-process secret; `verify()` uses a constant-time compare. Forged
  wrappers, mutated data and swapped timestamps all fail. `SanitizerRefusal`
  inherits from `Exception`, not `ValueError`, so a generic `except ValueError`
  upstream cannot silently swallow a refusal.
- **Mode-check ordering (A6).** Full DDL used to run *before* the marker was
  checked, so a wrong-mode open touched the wrong file before refusing. Now:
  meta-only bootstrap, read the marker, refuse before any DDL. A missing or
  tampered marker on a populated DB raises rather than restamping.
- **Seed determinism (A7, regressed, caught again in A9).** Five of six
  `Interaction` constructions fell back to pydantic's `default_factory=uuid4`,
  which uses `os.urandom` and bypasses the seed. A7 claimed this closed; the A9
  adversarial pass found it still open because **the A7 test hashed only the
  `applications` table**, which was clean. The reproducibility test now hashes
  every table. A test that checks one instance of a class of bug is how a
  regression stays invisible.
- **Deep-copy in `_transform` (A10).** `dict(payload)` was a top-level shallow
  copy, so a caller retaining a nested container could mutate it between
  `verify()` and send — the authenticator would still match while the bytes on
  the wire changed. Now snapshots through the same canonical JSON the HMAC uses.
- **One exclude list, not two (A10).** CI excluded `CHANGELOG.md` from the
  scanner and the local pre-commit hook did not, so `pre-commit run --all-files`
  refused commits on a tree CI accepted. Both now invoke
  `scripts/scan_tracked.sh`. This is the same single-sourcing argument that C1
  later applied to the rules themselves, and H-1 to the files that define them.
- **Supply chain (A10).** GitHub Actions pinned by full commit SHA with the tag
  in a trailing comment; `uv.lock` committed and CI running `--frozen`. Phase B
  introduces API-key secrets, so this had to be right before then.
- **Scanner coverage (A9).** The email regex was widened for IDN local parts,
  non-ASCII TLDs and Punycode — deliberately over-broad, per D7's "false
  positives are the design point." `--exclude` got a real glob-to-regex compiler
  after `fnmatch` was found treating `**` as literal, leaving `tests/**` a hole.
- **Safe surface (A9).** `connect`/`init_db` renamed to `_connect`/`_init_db`
  with `__all__` naming the safe surface and a lock test asserting the primitive
  cannot be re-exposed by accident. `busy_timeout` + WAL added.
- **A8** added the README's three-idea framing, `docs/design-principles.md`
  (D1–D10 inlined, each recording what was chosen and the alternative rejected),
  MIT license, and CI.

**Three CI hotfixes, one lesson.** The scanner went red on `uv.lock` hashes, on
a comment that quoted the very strings it was explaining, and on a stale action
pin. The first two were self-inflicted prose, not the regex being oversensitive
— see the convention recorded under B2. The lockfile incident also exposed that
`set -euo pipefail` did not propagate `xargs`'s exit through the pipeline on Git
Bash, so the wrapper reported success on failure; it now checks the exit code
explicitly.

**Left open from Phase A at the time, all closed by 2026-09-12** (see the Phase
B summary above): walkthrough #5 (ADR-001 framing, fixed), #6 (coverage badge,
killed), #7 (CHANGELOG split, killed, then replaced by compacting one phase behind), and L-json-default-sanitizer-1 (fixed once Phase C's fit-scoring
payload gave it a real trigger). Caught by the first backlog sweep
after sitting untouched across two phase boundaries — nothing here rides
silently through a third one now that the sweep exists.
