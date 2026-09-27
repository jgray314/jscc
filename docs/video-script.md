# Video walkthrough script (F2)

A 3–5 minute screencast script for the demo the README promises but doesn't
have yet ([Sample output](../README.md#sample-output) says so). Two columns:
what's on screen, what's said. Timings are targets, not a stopwatch contract —
adjust in the edit, not by rushing the talk.

**Before recording:** reset the fixture so the run is reproducible and matches
this script's output exactly.

```bash
rm -f data/synthetic.db
uv run jscc db init
uv run jscc seed --random-seed 42 --now 2026-08-28T12:00:00+00:00
```

**Profile for the recording:** `resolve_profile_path` prefers a private
profile over the tracked example in every mode, so if `config/profile.private.yaml`
exists on the recording machine, scoring and drafting will use it —
real comp target, real must-haves, real writing samples, on camera. Before
recording, either delete/move that file so synthetic mode falls back to
`profile.example.yaml`, or copy the redacted stand-in built for this purpose:

```bash
cp docs/demo-fixtures/profile.demo.yaml config/profile.private.yaml
```

`profile.demo.yaml` carries realistic scope and skills pulled from a resume,
with every identifying detail stripped (no real name, contact info, employer
names, or actual writing samples). Delete `config/profile.private.yaml`
after recording — it's gitignored, so it won't get committed, but it
shouldn't linger either.

The DLQ-resolve beat below pastes `docs/demo-fixtures/jd-posting-hybridized.txt`
— a fictional posting for a fictional company ("Vireo Systems"), hybridized
from three real, public postings (an ML platform EM role, an AI/ML platform
build-out, and an ML engineering leadership role) so it reads like a real
senior-EM posting without being any one real company's actual listing.
`jd-posting-fictional.txt` is a shorter fallback if the longer one runs the
DLQ-resolve beat too long in a take.

Terminal font large enough to read on a 1080p recording; dashboard browser
window at a plain 1280×800 or similar so nothing overflows off-frame.

---

## 0:00–0:20 — Open

**Show:** README.md at the top, scrolled to the one-line pitch and the
"Start here: five things worth reading first" list. Don't scroll further.

**Say:** "This is JSCC — a job search pipeline tracker with two LLM stages:
extraction and fit scoring, plus a follow-up drafter that only drafts the
routine cases and hands the rest to a person. Everything you're about to see
is a real command against a synthetic fixture — nothing staged, nothing
faked."

---

## 0:20–1:10 — Ingest, and the DLQ recovery path

**Show:** Terminal. Run:

```bash
uv run jscc dlq list
```

Point at the two unresolved entries (`blocked`, `timeout`) already in the
seeded fixture — these stand in for a posting the fetcher couldn't reach.

**Say:** "Not every posting is fetchable — paywalls, bot blocks, timeouts.
Those don't crash the pipeline or get silently dropped; they land here, in a
dead-letter queue, with the failure mode recorded."

**Show:** Run the resolve command against the `blocked` entry's id (copy the
real id `dlq list` just printed), pasting the hybridized posting from
`docs/demo-fixtures/jd-posting-hybridized.txt`:

```bash
uv run jscc resolve-dlq <entry-id> --paste-text "$(cat docs/demo-fixtures/jd-posting-hybridized.txt)" --company "Vireo Systems"
```

**Say:** "The recovery path is the same one line the dashboard's resolve form
calls — paste the text by hand, and it goes through the same extraction and
storage path a clean fetch would have. Nothing's lost, and there's no second
code path to keep in sync."

**Show:** `uv run jscc dlq list` again — the entry is gone from the
unresolved list.

---

## 1:10–1:50 — Score it

**Show:** Terminal. Run `jscc report --now 2026-08-28T12:00:00+00:00` to get
a stale application's id (or use the one just created), then:

```bash
uv run jscc score <application-id>
```

**Say:** "This scores fit against my profile — comp band, must-haves,
title-level match, all the judgment work I built a rubric for. No API key is
configured for this project, so what you're seeing live right now is the
stub client's placeholder — it exercises the same code path, but it isn't a
real model answer."

**Show:** Cut to `evals/README.md` or the README's Status table, specifically
the fit-scoring line: "27/28 in each of two rounds."

**Say:** "The real answer comes from hand-captured Claude chats, replayed
through the eval harness — that's next."

---

## 1:50–2:20 — Draft a follow-up

**Show:**

```bash
uv run jscc followup <application-id>
```

**Say:** "Same story — live, this calls the stub. But the drafter's real
behavior is worth showing, because it's the actual design point of this
project: it routes first, and only drafts the routine cases."

**Show:** README's "Sample drafter output" section — the routine draft next
to the non-routine briefing card, side by side if the editor supports a
split view.

**Say:** "A full-loop onsite thank-you gets drafted. A candidate deciding to
withdraw gets a briefing card instead — no draft, because that reply affects
a relationship and deserves a person's judgment, not an autocomplete."

---

## 2:20–3:10 — The eval harness

**Show:** Terminal.

```bash
uv run jscc eval composition --replay
```

Let the pass/fail list scroll briefly, then hold on the final line:
`25/28 passed (89%)`.

**Say:** "This replays 28 real, hand-captured Claude conversations against
today's grading code — no API key needed, no live spend. 25 out of 28 pass.
That's not a held-out number — the prompt and the grader were both tuned
against these same 28 cases — and the repo says so everywhere this number
appears, not just in the fine print."

**Show:** `evals/README.md`, scrolled to the round-by-round table for
composition (24/28 → 20/28 after a grader fix caught invented dates → 25/28
after two recalibrations).

**Say:** "The honest version of this story includes the two rounds that
missed the bar, and why — a grader that got stricter, then fairer."

---

## 3:10–3:40 — Cost and instrumentation

**Show:**

```bash
uv run jscc costs
```

**Say:** "Every call to a model goes through one path, and that path is
metered — tokens, latency, dollars, all captured at the call site. No real
dollar figures exist yet, because every call so far has been a stub or a
hand-captured chat, never a billed API request. What's built and tested is
the machinery — the honest claim today is that cost tracking works, not what
this costs to run."

---

## 3:40–4:10 — The dashboard

**Show:** Run `uv run jscc serve`, open `http://127.0.0.1:8000`. Click
through: funnel view → pipeline view → an application detail page → the DLQ
list.

**Say:** "Same data, same logic, rendered over HTTP instead of the terminal —
built on the exact same functions the CLI report command uses, so the two
can't disagree with each other about what's stale."

**Show:** Point at the "SYNTHETIC MODE" banner in the page header.

**Say:** "This banner is not cosmetic — this tool also runs against my real
job search data, in a completely separate, marker-stamped database, and the
banner is how I always know which one I'm looking at."

---

## 4:10–4:30 — Close

**Show:** README's "Architecture" section (the mermaid diagram) or the ADR
list, whichever renders more cleanly on screen.

**Say:** "Seven ADRs, ten design principles, and two cold review passes —
one adversarial, one an outside reviewer's read — at every phase boundary.
The repo's linked in the description if you want to see how any of this
actually works."

**Show:** Cut to black / end card with the repo URL.

---

## Notes for the edit

- Cut the stub-client caveats tighter if they feel repetitive back to back —
  say it fully once (score), reference it briefly the second time (followup).
- If `jscc serve` output looks empty or wrong on the recording machine, rerun
  the seed reset above — the dashboard reads the same `data/synthetic.db`
  the CLI commands populate.
- Don't record over a `JSCC_DATA=real` session by habit — check the mode
  banner / `[mode: ...]` prefix before pressing record, not after.
