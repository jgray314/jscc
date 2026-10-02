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

## Recording setup

**Tool: OBS Studio** (free, Windows). Chosen over the alternatives because the
script alternates between three views and OBS can switch between them with a
hotkey instead of a live window shuffle.

- **Why OBS:** per-view scenes; window capture (not full-desktop), so
  notifications, the taskbar, and anything else on the desktop stay off
  camera, which matters because the script warns about showing real data; and
  a separate mic audio track, so a flubbed line can be re-recorded in the edit
  without redoing the screen footage.
- **Output:** canvas and output 1920×1080, 30 fps. Format and encoder
  settings are in the next section.
- **Take structure:** record one take per beat (the timestamp headings below),
  not the whole script in one go. A bad take then costs 30–60 seconds, not the
  full run.
- **Alternatives, if OBS is too much setup:** Windows Snipping Tool
  (Win+Shift+R) is built in and fine for one clean take per section, but has
  no scene switching or separate audio track. Loom is easy but uploads to
  their cloud; keep this footage local.
- **Editing:** Clipchamp (built into Windows 11) or DaVinci Resolve (free)
  covers trims and the "cut the stub caveats" note at the bottom.

### OBS settings

Checked against the fresh install's config on 2026-10-02: it was still at
defaults (one empty scene, no hotkeys). Four things differ from what this
recording needs; the rest of the defaults are fine (1920×1080 base and output,
NVENC encoder, 48 kHz stereo, files to `C:\Users\scisp\Videos`).

| Setting (where) | Install default | Set to | Why |
|---|---|---|---|
| Video → Common FPS | 60 | **30** | Terminal and docs footage gains nothing from 60; halves file size and editing load |
| Output → Output Mode | Simple | **Advanced** | Simple mode records a single mixed audio track; separate mic track needs Advanced |
| Output → Recording → Audio Track | Track 1 only | **Tracks 1 and 2** | Track 1 is the mix, track 2 carries the mic alone so narration can be replaced in the edit |
| Output → Recording → Rate Control | Quality "Small" (Simple mode) | **CQP, CQ level 18–20** | "Small" softens terminal text; 1080p screen content at CQ 18 stays crisp and the files are still modest |

Other settings, not changed from the install:

- **Format:** the install's default, Hybrid MP4, is crash-safe and plays
  everywhere, so no MKV-then-remux step is needed.
- **Desktop Audio:** mute it for the whole recording. The terminal makes no
  sound, and muting it means notification dings can't land in a take.
- **Mic/Aux:** it's on the default device. Confirm in Settings → Audio that the
  default is the mic you mean to use, and in the mixer that the level peaks
  around −12 to −6 dB when you speak at normal volume.
- **Advanced → Audio tracks (Mic/Aux):** open Advanced Audio Properties
  (gear next to the mixer) and tick tracks 1 and 2 for Mic/Aux, track 1 only for
  Desktop Audio.

### Scenes

Set up four scenes, each with a hotkey. Each beat below is tagged with the
scene it starts in; a `→` marks a mid-beat switch.

| Scene | Source | Used for |
|---|---|---|
| **TERM** | Window capture: terminal only | Every CLI command |
| **DOCS** | Window capture: editor showing README / `evals/README.md` | README, status table, eval round tables, architecture diagram |
| **BROWSER** | Window capture: browser at ~1280×800 | `jscc serve` dashboard |
| **END** | Image source: `docs/demo-fixtures/end-card.png` (1920×1080, repo URL) | Final 2–3 seconds |

**Creating each source** (`+` under Sources, Window Capture):

- Set **Capture Method** to *Windows 10 (1903 and up)*. The default can show a
  black frame for a browser with hardware acceleration, and can miss a
  Windows Terminal window.
- Pick the window by title. Open the terminal, editor, and browser first so
  they appear in the Window dropdown; a window opened after the source is made
  has to be re-selected.
- Untick **Capture Cursor** on DOCS if the pointer wanders while you read; keep
  it on for TERM and BROWSER, where the viewer follows your clicks.
- Set each source to **Fit to screen** (Ctrl+F on the selected source) so a
  1280×800 browser fills the 1920×1080 frame instead of floating small.
- The **END** scene needs no window: add `docs/demo-fixtures/end-card.png` as an Image
  source.

### Hotkeys

Settings → Hotkeys. Search each scene name and bind its "Switch to scene"
entry. Hotkeys are global, so they work while the terminal has focus.

| Action | Hotkey |
|---|---|
| Switch to scene TERM | `Ctrl+Alt+1` |
| Switch to scene DOCS | `Ctrl+Alt+2` |
| Switch to scene BROWSER | `Ctrl+Alt+3` |
| Switch to scene END | `Ctrl+Alt+4` |
| Start Recording | `Ctrl+Alt+R` |
| Stop Recording | `Ctrl+Alt+S` |

Start and stop on a keypress instead of clicking the OBS window means the
click and the OBS window never touch the footage. Do a 10-second test take and
play it back before the first real one: check that scene switches land, the
text is readable at full screen, and track 2 holds the mic.

---

## 0:00–0:20 — Open

**Scene:** DOCS (whole beat).

**Show:** README.md at the top, scrolled to the one-line pitch and the
"Start here: six things worth reading first" list. Don't scroll further.

**Say:** "This is JSCC — a job search pipeline tracker with two LLM stages:
extraction and fit scoring, plus a follow-up drafter that only drafts the
routine cases and hands the rest to a person. Everything you're about to see
is a real command against a synthetic fixture — nothing staged, nothing
faked."

---

## 0:20–1:10 — Ingest, and the DLQ recovery path

**Scene:** TERM (whole beat). Switch DOCS → TERM on the first command.

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

**Scene:** TERM → DOCS (at the status-table cut).

**Show:** Terminal. Use the application id `resolve-dlq` printed in the
previous beat ("created application …"), then:

(Don't reach for `jscc report` to find an id: it prints no ids. And don't run
it with `--now 2026-08-28…` after the resolve beat: the new application is
stamped with today's date, so a pinned past `--now` makes `report` error out
with a "future reference timestamp".)


```bash
uv run jscc score <application-id>
```

**Say:** "This scores fit against my profile — comp band, must-haves,
title-level match, all the judgment work I built a rubric for. No API key is
configured for this project, so what you're seeing live right now is the
stub client's placeholder — it exercises the same code path, but it isn't a
real model answer."

**Show:** Cut to `evals/README.md` or `docs/technical-reference.md`'s Status
table, specifically the fit-scoring line: "27/28 in each of two rounds."

**Say:** "The real answer comes from hand-captured Claude chats, replayed
through the eval harness — that's next."

---

## 1:50–2:20 — Draft a follow-up

**Scene:** TERM → DOCS (at "Sample drafter output").

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

**Scene:** TERM → DOCS (at the round-by-round table, after holding on the
final `25/28` line).

**Show:** Terminal.

```bash
uv run jscc eval composition --replay
```

The summary, `25/28 passed (89%)`, is the **first** line of output; the
pass/fail list (35 lines, three `[FAIL]`s) follows it. Let the list scroll
past, then scroll back up (or `| head -1` in a second take) and hold on the
summary line.

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

**Scene:** TERM (whole beat). Switch DOCS → TERM on the command.

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

**Scene:** TERM (to start `jscc serve`) → BROWSER (as soon as the server is
up; cut the startup wait in the edit).

**Show:** Run `uv run jscc serve`, open `http://127.0.0.1:8000`. Click
through: the home page (Funnel, Pipeline, and Stale alerts are sections of
the one page, so scroll rather than click) → an application detail page → the
DLQ page.

**Say:** "Same data, same logic, rendered over HTTP instead of the terminal —
built on the exact same functions the CLI report command uses, so the two
can't disagree with each other about what's stale."

**Show:** Point at the "SYNTHETIC MODE" banner in the page header.

**Say:** "This banner is not cosmetic — this tool also runs against my real
job search data, in a completely separate, marker-stamped database, and the
banner is how I always know which one I'm looking at."

---

## 4:10–4:30 — Close

**Scene:** DOCS → END (switch at the last spoken sentence, hold 2–3 seconds).

**Show:** README's "Architecture" section (the mermaid diagram) or the ADR
list, whichever renders more cleanly on screen.

**Say:** "Seven ADRs, ten design principles, and two cold review passes —
one adversarial, one an outside reviewer's read — at every phase boundary.
The repo's linked in the description if you want to see how any of this
actually works."

**Show:** End card with the repo URL (scene END).

---

## Notes for the edit

- Cut the stub-client caveats tighter if they feel repetitive back to back —
  say it fully once (score), reference it briefly the second time (followup).
- If `jscc serve` output looks empty or wrong on the recording machine, rerun
  the seed reset above — the dashboard reads the same `data/synthetic.db`
  the CLI commands populate.
- The `score` stub line prints a `�` where its em dash should be (the Windows
  terminal's cp1252 codepage; the data is fine). Run `chcp 65001` in the
  terminal before recording, or accept it and don't linger on that line.
- `jscc followup` on the just-created application prints `HANDLE MANUALLY …
  (untitled)` from the stub router, not a draft. That's the stub's
  conservative default, and it lines up with the "stub" caveat in that beat;
  the real routine-vs-briefing contrast comes from the README's sample output.
- Don't record over a `JSCC_DATA=real` session by habit — check the mode
  banner / `[mode: ...]` prefix before pressing record, not after.
