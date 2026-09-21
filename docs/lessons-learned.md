# Lessons learned

What building JSCC with AI assistance taught me about keeping AI-assisted work trustworthy. This is the first batch: the lessons I can point to code, tests and commits for. More will be added as they are evidenced.

**Read this with the scale in mind.** This is one builder, one repo, no team, and no production traffic. Everything below is what happened here. Each lesson ends with a line on how it might carry to a team, and that line is an extrapolation, not a result. This is a starting point toward thinking about team practice, not a team playbook. The repo is supporting evidence for how I work; I'd expect to be asked about any of it and to defend it in conversation.

Related, and not repeated here: [how the phase gates work and six worked findings](gate-reviews.md), the [threat model](threat-model.md), and [ADR-005](../decisions/005-sanitizer-authenticity.md).

## 1. A control that depends on remembering is not a control

**What happened.** The design said an egress sanitizer redacted personal data, and the README repeated it. For several slices the redaction step was an empty placeholder while the claim sat in the docs (`972e54e`). When that was fixed, the fix itself depended on the process running from the repo root, so the two enforcement points drifted apart again through path resolution (`9beabff`). A prose claim in the README about the test count went stale four times until a test made the drift fail the build (`3c1e408`).

**What worked.** Each time, the durable fix was to make the wrong state impossible or loud, not to add a note saying to be careful: one shared definition of "personal" used by both enforcement points, one package-anchored root for every path, a test that fails when the README's count is wrong.

**For a team, as a hypothesis.** When a review finding says "make sure people remember to X", the closing action should be a check that runs, not a comment. If it cannot be checked, say in the doc that it is a convention.

## 2. AI-written tests can look right and prove nothing

**What happened.** Four kinds of test passed while unable to fail: a CLI test that mocked the function it was meant to test (the crash it should have caught shipped, `6e601a1`); redirect-guard tests that stayed green when the guard was switched off; a "the fixture is scrubbed" test over a binary file the scanner skips entirely; and two "does not leak" assertions that a plain string is absent from a hash digest, which is always true (all four are in [gate-reviews.md](gate-reviews.md), finding 3, `43ef39b`). A fifth kind turned up at the Phase D gate: tests that asserted the bug. One confirmed that the composition eval passes when the bar is lowered, the hole in the gate; another confirmed that the cost report drops a feature whose calls all failed. Both were replaced (`85aeb0e`).

**What worked.** Verify by breaking it. For any guard, delete or invert the guard and confirm a test goes red. I applied the same rule to the test that enforces that every model call goes through the sanitizer: I injected three regressions and watched it fail each time (`7bdb7c4`). It still had a blind spot. It only scanned the top-level package, so when a later refactor moved the CLI into a subpackage, the test stopped seeing it. The Phase D gate caught that. The test now scans subpackages, and a separate test proves it finds a nested caller (`4bdd28c`). The lesson I took: break a guard in the ways the code is likely to change later, not only in the ways it could break today.

**For a team, as a hypothesis.** Fast, AI-generated tests are cheap to write and easy to trust. A review question worth asking of any new guard test is "what happens if I remove the thing it guards?" A periodic sweep for tests that no longer earn their keep is a reasonable companion.

## 3. One eval number is a band, and the suite has to be sized first

**What happened.** The extraction prompt measured 76% and 82% on two independent capture rounds with the prompt unchanged. That spread is model variance under manual capture, so the repo reports a band and does not pick the better number ([evals README](../evals/README.md), `bd8ae93`). Sizing the suites came up repeatedly: 15 cases left roughly ±10 points of noise, so extraction was grown to 33 before any capture effort was spent (`5aeab63`); the routing suite was undersized in the same way and the gap was found only three cases into a capture round (`41b6495`).

**What worked.** Doing the standard-error arithmetic before collecting anything, writing down what the interval does and does not say, and stating that even 33 cases is "less noisy", not statistically tight.

**For a team, as a hypothesis.** A single pass rate from a small suite invites false precision. Asking "how many cases, and what is the interval?" before trusting a number, and reporting a range when you have two measurements, costs little.

## 4. Cheap proxies decide whether a real check is worth running, and never replace it

**What happened.** The routing classifier was tuned with fast subagent runs standing in for real chat completions. The final wording scored 26/26 with zero false-routine cases on those proxy runs. The next real round, captured by hand, scored 23/26 and failed the gate on exactly the kind of case the proxies had passed (`bfbd9b1`, [evals README](../evals/README.md)). After a wording fix and a fixture correction, the round after that scored 26/26. That was the fifth round on the same 26 cases, with the prompt tuned after each failing one, so it shows the prompt handles those cases, not a rate for new ones. The next routing capture adds fresh held-out cases.

**What worked.** The standing rule: proxies are a filter for whether a real round is worth the effort, and their output never goes into the recorded fixtures. The real round was the only evidence that counted, and it caught something the cheap loop missed.

**For a team, as a hypothesis.** An AI-run stand-in for a human or production check is useful for iteration speed, but the sign-off needs the real thing, and the stand-in's output should be kept out of anything that claims to be real.

## 5. A deferral needs a trigger that something actually checks

**What happened.** ADR-005 recorded a decision to defer a stronger type-level guard, with a stated trigger: revisit when a second module calls the model client. The trigger fired when the scorer landed in Phase C and again for routing and composition. Nothing noticed, because nothing watched for it, and the gate that reviewed call sites checked only the ones that existed then. I found it while writing the threat model, three phases later. A related case: an earlier review rated a DNS-rebinding gap "reasoned, not exploited" and closed it with a note, and a later review overturned that rating (`b9d1a91`).

**What worked.** The first fix was a test that fails when a new caller appears or an existing one skips the boundary, plus an ADR addendum recording the miss and a new trigger (`7bdb7c4`). That test missed callers in subpackages (lesson 2), and the Phase D gate replaced the list of approved callers with a single path: every stage now reaches the model through one function, the only code allowed to call the client, and the test checks every module in the package, subpackages included (`4bdd28c`). The deeper change is treating a trigger as something that must be checkable by a test, a gate step or a backlog sweep, not a sentence in a document.

**For a team, as a hypothesis.** A deferred item with a prose trigger and no owner is a way to forget something politely. Either make the trigger executable, or put the item where a recurring process reads it.

## 6. Slice size mattered more than slice estimates

**What happened.** The estimates for individual slices were often wrong. The size of the slices still turned out to be a good size for the components. Each one was small enough to finish and check on its own, which gave natural points for light validation and check-ins. The same boundaries gave the work a structure for managing model scope, including compaction: a slice end is a clean place to save state, reset context and pick up again. It also let me take the work in discrete chunks instead of one long push.

**What worked.** Treating the slice boundary as the unit for everything else: validation, a check-in, a written handoff and a context reset all happen there. The estimate being off did not matter much, because the boundary was useful whatever the estimate said.

**For a team, as a hypothesis.** When you break AI-assisted work up, size the pieces for how easy they are to check and hand off, not for how well you can predict their effort. A team member picking up a piece, or reviewing it, benefits from the same clean edges.

## 7. A general observation: "one more turn"

This style of project work has a bit of the Civilization (game) "one more turn" structure: a short wait, then a reward, and the next step is always right there. Each slice finishes quickly and shows a visible result, and that makes it easy to keep going. I'm noting it as an observation, not a conclusion. I have no measurement of what it did to the work, and I haven't yet separated the parts that helped from the parts that cost something.

## What this does not show

- **Anything about a team.** No adoption, change management, review load, junior development or onboarding. Each "for a team" line above is a guess I'd want to test.
- **Production behaviour.** There is no live traffic and no API key configured, so there are no real cost or latency figures.
- **Independent review.** The gate reviewers were all one model family, and no human outside the project has reviewed the work.
- **A measured productivity effect.** I have observations about how estimates and actual effort compared, and a script that estimates active time from commit history with its biases stated ([scripts/active_time.py](../scripts/active_time.py)). I am holding those back until there is more data and I can state them properly.

## Still to come

Lessons I expect to add once I can back them: how AI estimates of its own work and of manual work compare with what happened; how much of the effort went to gates and hardening; what else planning and breaking work into slices changed beyond slice size; how confidence in letting the AI run unattended grew, and what earned each step; and how much difference bringing a key insight early made, with the eval-suite sizing as the example.
