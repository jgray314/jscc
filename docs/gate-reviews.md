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

**Note on the counts.** They are approximate. Some findings were merged, split or renumbered while being fixed, a few were renumbered to avoid colliding with an earlier gate's IDs, and the Phase A rounds are tallied from the changelog rather than from the original review documents. Treat them as scale, not as exact figures.

## Six findings worth reading

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

This is why the eval suite is not wired into CI as a gate. Replay pins the harness, the parser and the output contract. It does not measure model judgment, and until this fix it did not even pin the prompt.

### 5. A rating that was wrong

*Found in the Phase C to D gate. Fixed in `b9d1a91`. Labelled CONTRADICTS.*

An earlier pass had noticed that the fetcher checks a hostname's resolved address and then lets the HTTP library resolve it again to connect. It rated this low-risk, "reasoned not exploited", and closed it by documenting it as a residual. The next gate re-examined it with a concrete attack: a short-lived DNS record answers the check with a public address and the real connection with a private or metadata one. That is a standard rebinding attack, and the earlier rating did not survive it.

Every request, including each redirect hop, is now pinned to the addresses already validated. The design note that had called the gap acceptable now says the earlier call was wrong. The point is less that a hole existed than that a documented decision to accept a risk was itself reviewable, and was reversed.

### 6. A fix that silently regressed

*Found in the Phase A rerun. Fixed in `2f40b81`.*

An earlier fix had made the seeded fixture reproducible by threading a seeded random source through every model construction. Five later constructions still fell back to a random default. The regression test only queried one table, which was clean, so the miss was invisible. The test now hashes every table.

## Where the process falls short

- **A slice can be missing and no gate notices.** LLM call instrumentation slipped past all three Phase A rounds because every round reviewed code that shipped, and none had a reason to look for what was not there. I caught it re-reading the plan before starting Phase B.
- **Status prose goes stale within hours.** The README contradicted itself about what had shipped in three separate passes. A test now keeps the test count honest, but nothing checks the rest of the status text, and the cheap fix so far is dating it.
- **Severity ratings are judgments and can be wrong.** Finding 5 is the example. A reviewer's Low and my agreement with it did not make it Low.
- **This is one author and one model family.** All reviewers were Opus agents, and much of the code they reviewed was written with AI assistance, so they may share blind spots with it. No human outside the project has reviewed it. The cold-read rule and verify-by-deletion reduce that. They do not remove it.
- **The number of findings is a property of the process, not a quality score.** A reviewer told to find problems finds them, so a long list says as much about the instructions as about the code.

## Open items

None of the findings above remain open. The current backlog lives in the CHANGELOG. Items deferred with a stated revisit trigger, rather than closed, include one minimization finding for the scoring payload and a second validation round for fit scoring; both are named in the Phase C to D gate entry of the CHANGELOG.
