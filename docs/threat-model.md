# Threat model

One page on what this tool protects, from whom, how, and what is still open. It is a working document, written after the controls, from the code and the gate findings. The design principles it draws on are [D6, D7 and D8](design-principles.md); the review history is in [gate-reviews.md](gate-reviews.md).

## Scope and assumptions

JSCC is a local, single-user CLI. It fetches job postings, sends text to an LLM, and stores results in a local SQLite file. The user is trusted. The machine is assumed uncompromised. The one listener is the optional local dashboard (`jscc serve`, bound to loopback by default; T13). There is no multi-user access and no email sending: the drafter produces text for the user to review.

Not in scope: a malicious local user, a compromised host, the model provider's own retention and handling of data it receives, and free-text detection of names in prose (see T1).

## Assets

| Asset | Why it matters |
|---|---|
| Contact details for recruiters, hiring managers and referrers (names, emails, phones) | Real people who did not agree to have their details in a third-party model or a public repo |
| The user's profile (compensation target, deal-breakers, writing samples) | Private, and it is sent to the scoring stage |
| API credentials | Direct financial and account exposure |
| Integrity of the pipeline record | The tool's outputs drive real decisions about where to apply and what to send |
| The network the CLI runs on | The fetcher makes requests to URLs it did not choose |

## Trust boundaries

```
untrusted web page / pasted text
        │  (T4 fetch, T5 injection)
        ▼
   fetcher ──► local SQLite (real.db, git-ignored) ◄── the user's own notes
        │                          │
        ▼                          ▼
  sanitizer (redact, then authenticate)  ──►  git (pre-commit scanner)   (T1, T2, T8)
        │
        ▼
   LLM (no tools, single turn)  ──►  parsed into typed, bounded fields  (T5, T6)
        │
        ▼
   local display / a draft for the user to read and send by hand         (T12)
```

The model has no tools, no network access and no way to act. Its output is parsed into typed fields and shown locally. That is the main reason the prompt-injection risk (T5) is contained rather than serious.

## Threats

Status: **controlled** means enforced in code with a test, **partial** means a control exists with a stated hole, **open** means no control yet.

| # | Threat | Control | Residual and status |
|---|---|---|---|
| T1 | Contact identifiers reach the LLM | Sanitizer redacts emails, phone-shaped digit runs, API keys, danger-list terms and the full names of the application's stored contacts (loaded by the routing and composition stages themselves) before the payload is authenticated. Each string field is redacted before the payload is serialized, so a name outside ASCII matches its danger-list entry. No caller can opt out. `jscc/personal_data.py`, `jscc/sanitizer.py`, ADR-005 | Does not detect an unfamiliar name in free text; that needs NER, not regex. A stored contact mentioned by first name only is not redacted: contact substitution matches the full name, because a bare first name would also rewrite ordinary words that contain it. Redaction exemptions for a `model` key apply at any depth, and non-string values are never redacted (both documented as TODO, unreachable with today's flat payloads). Phone-shaped requisition IDs over-redact by design. **Partial, scope stated in D8** |
| T2 | Personal data committed to git | Pre-commit scanner and the same CI script use the same definition of "personal" as the sanitizer, and read the same danger lists from a package-anchored path | The scanner skips any file it cannot decode as text, so a database is protected by being untracked, not by being scanned. **Controlled, with that limit** |
| T3 | Real and synthetic data mixed, or the real DB created outside the ignore rules | Two DBs stamped with a mode marker; opening in the wrong mode raises; data paths anchored to the package, not the working directory (ADR-003) | **Controlled** |
| T4 | The fetcher is used to reach internal addresses (SSRF), including via redirects or DNS rebinding | http(s) allowlist, rejection of any host resolving to a non-public address, the check repeated on every redirect hop, each request pinned to the addresses already validated, proxy environment variables ignored so no proxy re-resolves the host, a redirect ceiling and a 5 MB streamed cap (D6) | Internationalized hostnames are compared and pinned in their punycode form, the name the connection layer actually resolves (a pin keyed on the URL's own spelling missed them). The optional Playwright fallback follows its own redirects outside these guards, resolves the original host itself unpinned, and runs the page's JavaScript, so a hostile page rendered there can make requests to internal addresses; that is why it is off by default. A machine that needs a proxy to reach the web cannot fetch at all; those postings go to the DLQ for manual paste. NAT64-encoded private addresses are not filtered (needs a NAT64 gateway to exploit). **Controlled for the default path (including internationalized hostnames), partial for the browser fallback, which is off by default and Medium if enabled** |
| T5 | A hostile job posting contains instructions aimed at the model | The model has no tools, so it cannot act. Output must parse into typed models with bounds (for example a fit score outside 0 to 100 is rejected). Sanitization is independent of the model's behavior. Drafts are reviewed by a person and never sent automatically | **Partly validated at the prompt level.** Both prompts say the posting is data, and each suite has three hostile-posting cases. All six passed in every real-chat round: extraction 3/3 on its final round, scoring 3/3 in each of two rounds, each hostile score staying in the band its real fit deserves. This is six fictional cases on one model family each and fixtures the prompts were written against, so it shows the prompt wording works on these shapes, not that injection is solved. Since 2026-09-20 the routing and composition payloads withhold the posting text, its URL, the extracted fields and the fit rationale, enforced by test, so a posting cannot argue the router toward "routine". Injected text still reaches extraction and scoring. Realistic impact there is a distorted score or wrong extracted fields, caught by a human read. See "Next" |
| T6 | Model output is malformed or hostile | Fence-tolerant parsing into typed models, truncation distinguished from bad JSON, score bounds, and a failed parse routes to the dead-letter queue instead of crashing. Every command prints through one helper that strips control characters (C0 and C1), enforced by a test that fails if a command module calls `click.echo` directly | **Controlled** |
| T7 | An API key leaks into a prompt or a commit | Keys are matched by both the sanitizer and the scanner; `.env` files are ignored. No key is currently configured for this project | **Controlled** |
| T8 | A code path sends text to the model without sanitizing it | The send boundary verifies an HMAC-authenticated payload, so a forged or mutated payload is refused. All four stages call the model through one function, `stage_call.call_stage`. `tests/test_llm_egress.py` scans every module under `jscc/`, subpackages included, and fails if any module other than `stage_call` calls the model client, if `call_stage` verifies before it sanitizes or hands the client anything not derived from the verified payload, if a stage stops going through `call_stage`, or if a stage still reaches its client after the boundary refuses. Each check was confirmed by injecting the regression and watching it fail (ADR-005, second addendum) | Enforced by inspection, not by type: the client's `complete` still takes three plain strings. A second module calling it fails the test, but the test cannot tell a correct `call_stage` from one that only looks correct. On 2026-09-20, between the CLI becoming a package and the Phase D gate fix, the scan did not descend into `jscc/cli/`, so a caller there would have passed. An authenticated argument type is the stronger fix and is the trigger recorded in the ADR. Until 2026-09-20 nothing enforced this at all, although ADR-005's trigger for it had fired when the scorer landed. **Controlled, by test** |
| T9 | A compromised dependency or CI action | GitHub Actions pinned to commit SHAs; CI installs from a frozen lockfile | The Playwright browser binary is a separate download. **Partial** |
| T10 | A transient failure loses a posting the user already fetched | Transport and parse failures write a dead-letter entry, exit with a distinct code and can be retried | The raw fetched text is not persisted before extraction is attempted. **Partial, decision open** |
| T11 | Runaway model spend | Every call is metered at the call site, an unknown model's price raises instead of defaulting, and `jscc costs` flags rate mismatches | Nothing refuses a call over a budget; the cap is planned, not built. **Open** |
| T12 | The drafter produces something it should not, or acts on it | No send capability. A router sends unrecorded-fact and other non-routine cases to a person, with a zero-tolerance gate on wrongly auto-drafting; the composer can decline rather than invent a fact, with its own zero-tolerance gate: a draft on any case that required it to ask fails the suite whatever the pass rate. Both gates have tests that fail when the gate is removed | Composition passed one manual capture round (24/28, all 3 decline cases correct); one round is a first data point, not a demonstrated range. **Partial** |
| T13 | A web page in the user's own browser reads or writes the local dashboard (DNS rebinding, or a cross-site form post to the resolve action) | Every request's `Host` must be on an allowlist (loopback names, plus the address `jscc serve` was told to bind), which refuses a rebound attacker domain; a state-changing request with a foreign or `null` `Origin`, or a cross-site `Sec-Fetch-Site`, is refused; no page loads a script or any third-party origin (a test fails if one is added); a stored URL is linked only if it is http(s); a resolve is serialized per entry and its final write is a compare-and-set, so a double submit makes one Application and one model call | The dashboard has no login: any process on the machine can use it (the machine is trusted, see Scope). Binding to a non-loopback address exposes it to that network with only the Host check. A resolve racing from a second process can still spend a model call before losing the compare-and-set. **Controlled for the browser-borne case** |

## How this maps to common frameworks

Approximate, for orientation. T1 and T7 sit under sensitive information disclosure. T5 is prompt injection, T6 is improper output handling, T9 is supply chain, T11 is unbounded consumption and T12 is excessive agency (OWASP Top 10 for LLM applications, 2025 list). For prompt injection specifically, a useful check is whether one component combines private data, untrusted content and a way to send data out. Here the scoring stage holds the first two (the profile and the posting), but there is no exfiltration channel: no tools and no network from the model, and output goes to typed local fields. Adding a tool, an auto-send or a link the model can cause to be fetched would break that and require redoing T5.

## Next, in order of value

1. **T8, type-level fix.** Only if the number of callers grows or someone other than the author writes one: wrap the client's arguments in an authenticated type, as ADR-005 anticipated.
2. **T5, evidence** (partly closed 2026-09-24). Both prompts say the posting is data, and three hostile-posting cases each sit in the extraction and scoring suites (an injected level and comp, an injected skill list, a schema override; a top-score demand, a deal-breaker override, an instruction carried into the extracted fields). All six passed on real Claude.ai chats: extraction (Haiku 4.5) 3/3, scoring (Sonnet 5) 3/3 in each of two rounds. "Reveal the profile" was dropped as a case: the output goes only to the user who owns the profile, so there is no one to reveal it to. Still open: more injection shapes than these six, a held-out set the prompts were not written against, and a re-run whenever either prompt changes.
3. **T10 and T11**, once a live key exists: persist fetched text before extraction, and add a spend cap.

## Limits of this document

It was written by the author from the code and the gate findings, then checked against the source. It has not had an independent security review or penetration test, and the gate reviewers were all one model family. It describes today's behavior. Anything marked partial or open is a real gap, not a formality.
