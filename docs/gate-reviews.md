# Gate reviews

At every phase boundary this project stops and gets reviewed before the next phase starts. This page explains how the reviews work, what they found, and where they were wrong. The full slice-by-slice record is in [CHANGELOG.md](../CHANGELOG.md); the commits cited below are in this repo's history.

It is written for a reader deciding whether the process is real. The short version: it found problems I had written into the code myself, including some in fixes I had just shipped, and it was wrong at least once about severity.

## How a gate works

Each gate runs two lenses over everything shipped since the last one.

- **Adversarial.** Looks for correctness and safety holes: bypasses, data leaks, contracts that the code does not actually keep. Every finding is reproduced against the code, not just read.
- **Outside-reviewer walkthrough.** Reads the repo cold, the way an engineering director skimming before an interview would. It is not a bug hunt. It asks what a skeptical reader would immediately push on: README claims, test names that overclaim, prose that describes a system slightly better than the one that exists.

Rules the gates run under:

- **Cold reads.** From the second gate on, each reviewer forms its own findings before it is allowed to see any earlier gate's list, then labels every finding **NEW**, **REPEAT-OF** an earlier one, or **CONTRADICTS** an earlier fix or decision. Reviewers who start from the previous list tend to confirm it. The earlier gates ran with prior findings in context; the first cold rerun produced a CONTRADICTS against a fix from the slice under review.
- **Severity tiers, and High blocks the next phase.** A High has to be fixed, or the risk accepted in writing, before the next phase starts.
- **Structural fixes over discipline.** A finding that says "remember to do X" is not closed by adding a comment saying to remember. It is closed by making the wrong thing impossible, or by a test that fails when it happens.
- **Fixes are verified by breaking them.** For guard-style fixes I delete the guard and confirm a test goes red. A fix whose removal leaves the suite green is treated as not yet fixed.
- **Open items get a disposition, not a shrug.** Each is fixed, decided and documented with a revisit trigger, or killed. Nothing rides silently through a boundary.

The reviewers are Opus-model agents run as separate sessions, chosen for simplicity. I re-verify their findings against the code before acting, and I make the calls on what to fix, defer or accept. A stronger setup would use different classes of models across the lenses, plus human reviewers.

## What ran

| Gate | Scope | Findings, roughly | Outcome |
|---|---|---|---|
| Phase A, three rounds | A1 to A10 | Round one: 6 critical, 4 high, 3 medium. Round two: 1 new critical. Round three: 1 high, 5 medium, 3 low | All critical and high resolved structurally |
| Phase B to C, first pass | Phase B slices | 1 critical (latent), 2 high, 5 medium, plus walkthrough items | Fixed in the following slice |
| Phase B to C, second pass (first cold rerun) | Everything after the fixes | 4 high, 6 medium, 10 low, 10 walkthrough | Produced the first CONTRADICTS |
| Phase B to C, third pass | After the backlog closed | 2 high, 6 medium, 8 low | All closed or documented |
| Phase C to D | Fit scoring and cost reporting | 1 high, 2 medium, 2 low, 4 walkthrough | High fixed, and it reversed an earlier rating |
| Phase D | Routing, composition, follow-up briefing | 4 high, 5 medium, 4 low, plus 16 walkthrough (3 high, 2 duplicating adversarial findings) | All fixed in three hardening slices. One high was a regression the gate's own backlog fix introduced, and one reversed an earlier fix |
| Phase E | Dashboard, the fetcher, and the extraction, scoring and routing prompt recaptures | Adversarial: 3 high, 4 medium, 11 low. Walkthrough: 5 high, 6 medium, 4 low (two duplicating adversarial findings) | All fixed or dispositioned in a hardening slice, two fix slices and a routing bundle. 7 findings contradicted an earlier "fixed" or "verified clean" entry. The routing one took two full recaptures and a code check, because prompt wording alone did not stop an injected note from steering the model |
| Phase F prep (scoped slice, not a phase boundary) | The composition weekday/relative-date fix and its two grader recalibrations | Adversarial: 0 high, 3 medium, 5 low. Walkthrough: 2 high, 5 medium, 9 low | All fixed same-day. Both highs were documentation-honesty issues (miscounted misses repeated in 5 places; a possible regression from this slice's own prompt fix explained away as "pre-existing" without checking) rather than code bugs; the strongest finding — mojibake in 6 of 28 recordings — was found independently by both lenses |
| Independent review, 2026-09-28 (scoped slice, first non-Claude reviewer) | The three structural-safety claims — LLM-egress redaction, real/synthetic mode isolation, fetcher SSRF guarding — cold-read against code only, no design docs | Adversarial (ChatGPT, one lens, no walkthrough): 1 critical, 2 high, 3 medium, 1 low | Critical and one high already-documented residuals, confirmed rather than new; one high genuinely new and fixed same-day; one medium re-scoped after checking the actual call sites, not fixed |

**Note on the counts.** They are approximate. Some findings were merged, split or renumbered while being fixed, a few were renumbered to avoid colliding with an earlier gate's IDs, and the Phase A rounds are tallied from the changelog rather than from the original review documents. Treat them as scale, not as exact figures.

## Seven findings worth reading

### 1. A safety claim the code did not keep

*Found in the first Phase B to C gate. Fixed in `972e54e`.*

The design said an LLM-egress sanitizer redacted personal identifiers, and the README repeated it. The sanitizer authenticated payloads but redacted nothing: its transform step was an identity function left over from an earlier phase. A string with a name, email and phone passed through untouched, while the same string was blocked from git by the pre-commit scanner. One protection was real and the other verified its own signature over unredacted content.

The fix was to define "personal" once, in `jscc/personal_data.py`, and have both enforcement points import it: the scanner to detect, the sanitizer to rewrite. Redaction runs before the payload is authenticated, unconditionally, so no caller can opt out of it. The claim was also narrowed to what the code does: structured identifiers and known names, not free-text name detection.

### 2. A fix that reintroduced the same drift

*Found in the second Phase B to C pass. Fixed in `9beabff`. Labelled CONTRADICTS.*

The previous fix claimed that adding a term to the local danger list blocks it in both git and LLM traffic with one edit. That was true only when the process ran from the repo root. The scanner always ran from the root, and the sanitizer ran from wherever the user was standing, so from another directory it silently read an empty list. Two egress points drifted again, this time through path resolution instead of duplicated regexes. The same root cause let `JSCC_DATA=real` create a database outside the `.gitignore` that protected it.

Both reviewers found it independently. The fix was one package-anchored root that everything resolves against, and it uncovered a third drift on the way: the scanner had never read the local danger list at all. The lesson I took: sharing the rules was never enough. Two enforcement points must also agree on where the rules live.

### 3. Tests that could not fail

*Found across several passes. Fixed in `43ef39b` and follow-ups.*

- The tests for the fetcher's redirect guard patched the HTTP call wholesale. Flipping the guard argument from off to on left all 30 fetcher tests green. The fix asserts that exactly one request leaves the process for a redirect into a private address, and I verified it by flipping the guard and watching it fail.
- A test claimed to prove a database fixture was scrubbed of personal data. The scanner skips any file it cannot decode as text, so scanning a binary database scanned nothing, and the assertion could only ever pass.
- Two "does not leak" tests asserted that a plain string was absent from a SHA-256 hex digest, which is true whatever the code does.
- A CLI test mocked the function it was supposed to be testing, so it proved the caller handles a result and never that the callee produces one. That gap is how a crash on empty response bodies shipped.

These are the reason "verify by deletion" is a standing rule. AI-written checks in particular can look right and prove nothing.

### 4. A replay suite that ignored the prompt

*Found in the third Phase B to C pass. Fixed in `1322985`.*

Eval recordings were keyed on the user prompt only. The docstring said the key covered changes to the prompt; it did not. I replaced the entire system prompt with an instruction to return the word "banana" and re-ran the suite: 27 of 33 before, 27 of 33 after, no error. The replay reported the published pass rate for a prompt that did nothing. The key now hashes model, system prompt and user prompt, and the same experiment fails every case loudly.

This is why the CI check on the recordings is a replay pin, not a measurement. Replay pins the harness, the parser, the output contract and the published numbers. It does not measure model judgment on new input, and until this fix it did not even pin the prompt.

### 5. A rating that was wrong

*Found in the Phase C to D gate. Fixed in `b9d1a91`. Labelled CONTRADICTS.*

An earlier pass had noticed that the fetcher checks a hostname's resolved address and then lets the HTTP library resolve it again to connect. It rated this low-risk, "reasoned not exploited", and closed it by documenting it as a residual. The next gate re-examined it with a concrete attack: a short-lived DNS record answers the check with a public address and the real connection with a private or metadata one. That is a standard rebinding attack, and the earlier rating did not survive it.

Every request, including each redirect hop, is now pinned to the addresses already validated. The design note that had called the gap acceptable now says the earlier call was wrong. The point is less that a hole existed than that a documented decision to accept a risk was itself reviewable, and was reversed.

### 6. A fix that silently regressed

*Found in the Phase A rerun. Fixed in `2f40b81`.*

An earlier fix had made the seeded fixture reproducible by threading a seeded random source through every model construction. Five later constructions still fell back to a random default. The regression test only queried one table, which was clean, so the miss was invisible. The test now hashes every table.

### 7. What a different model family actually found

*2026-09-28, a scoped slice, not a phase boundary. ChatGPT reviewing three files' worth of the sanitizer, mode isolation and fetcher, cold — no design docs, no prior findings, no walkthrough lens.*

Most of what it found was already known and already disclosed: the Playwright fetch fallback bypassing the SSRF guards (documented in `docs/threat-model.md` T4, off by default), the sanitizer's `model`-key exemption applying at any nesting depth (a `TODO (documented residual, not fixed)` comment in `sanitizer.py` naming the exact shape), and the redaction detector's stated scope boundary (structured patterns, not arbitrary names — D8's own wording). Independent confirmation of a residual is real signal — it means the residual was accurately described, not just asserted — but it is not new information, and none of it should be counted as evidence this pass found something Claude gate rounds could not.

One finding was new: `redact()`'s `name_roles` substitution spliced the role value straight into the replacement token without running it through the same email/phone/credential rules the rest of the text gets. `Contact.role` is a closed enum in every live caller (`jscc/models.py`'s `ContactRole`), so nothing in this codebase can reach it today — but the function's own signature accepts any string, and the fix (sanitize the role value before splicing it in) costs nothing and removes a control that only held because no current caller violates it. Fixed same-day (`jscc/personal_data.py`), alongside the `model`-key depth fix — cheap enough, and confirmed independently enough, that there was no reason to leave either as a documented residual once a second reviewer had found them without being told.

One finding was re-scoped after checking the actual call graph rather than accepted at face value: it read `send_to_llm` returning a bare `dict` as meaning any caller could mutate it and resend without re-verifying. True of the function's return type in isolation, but `stage_call.call_stage` — the only production caller (`tests/test_llm_egress.py` enforces this) — uses the dict immediately, in the same stack frame, before it can be mutated by anything else. The gap is real at the API-contract level (nothing stops a *future* caller from holding the dict and mutating it before use) and is already this project's own stated T8 residual and ADR-005 trigger ("an authenticated argument type is the stronger fix... only if the number of callers grows or someone other than the author writes one"). Re-scoping a finding after checking the real call sites, rather than either dismissing it or accepting its most alarming reading, is the same discipline this doc asks of every other pass.

The clearest value of this round wasn't a new hole — it was that the *concrete attack scenario* it wrote out for the Playwright bypass (`page.goto()` reaching an unpinned, unguarded browser navigation) was sharper and more usable than this repo's own prose about the same gap, which is now folded into `docs/threat-model.md` T4.

## Where the process falls short

- **A slice can be missing and no gate notices.** LLM call instrumentation slipped past all three Phase A rounds because every round reviewed code that shipped, and none had a reason to look for what was not there. I caught it re-reading the plan before starting Phase B.
- **Status prose goes stale within hours.** The README contradicted itself about what had shipped in three separate passes. Tests now keep the test count and the published eval figures honest, but nothing checks the rest of the status text, and the cheap fix so far is dating it.
- **Severity ratings are judgments and can be wrong.** Finding 5 is the example. A reviewer's Low and my agreement with it did not make it Low.
- **This is one author, and was one model family until 2026-09-28.** Every phase-boundary gate is still Opus agents reviewing code substantially written with AI assistance, so they may share blind spots with it. The one cross-model round run so far (finding 7) mostly confirmed residuals this project had already found and disclosed itself, which is real evidence the disclosures are accurate but not evidence that a second model family surfaces a different class of problem — one scoped round on three files is not a standing practice, and no human outside the project has reviewed any of it. The cold-read rule and verify-by-deletion reduce the single-family risk. They do not remove it.
- **The number of findings is a property of the process, not a quality score.** A reviewer told to find problems finds them, so a long list says as much about the instructions as about the code.

## Open items

None of the findings above remain open. The current backlog lives in the CHANGELOG. The invented-weekday/relative-date grader check, listed here as deferred in earlier passes, closed at the Phase F prep gate (see the "What ran" table above and `evals/README.md` for detail). **Composition's second validation round did not close there and is still open** — round 2 recaptured all 28 cases on a rewritten prompt (fixing the invented-weekday/relative-date defect), which makes it the first, not the second, round of that prompt; round 1's recordings are on the prior prompt and only replay against today's grader as a like-for-like check, not a second round of the current one. ADR-006's two-round rule is not met (`evals/README.md`, `docs/threat-model.md` T12). Items still deferred with a stated revisit trigger: three composition misses found investigating that round (`reschedule-accept` 3 words under the `body_length` floor, `second-cadence-nudge` over the `style_reuse` bar on two reused sentences, `coordinator-scheduling-ack`'s wrong escalation — each a single occurrence with no repeat, revisit at the next capture round), extraction-error propagation (end-to-end fixtures, and composition still receiving the extracted title and company), more hostile-posting shapes, and a held-out set for extraction and scoring, which have none (routing gained one at the Phase E gate; see the [evals README](../evals/README.md)). The scoring-payload minimization, fit scoring's second round and routing's held-out set were closed at the Phase E gate.
