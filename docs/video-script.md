# Video walkthrough script (F2)

A 3–5 minute screencast script for the demo the README promises but doesn't
have yet ([Sample output](../README.md#sample-output) says so). Two columns:
what's on screen, what's said. Timings are targets, not a stopwatch contract —
adjust in the edit, not by rushing the talk.

**Shell:** run every command in this script in **PowerShell**, not Command
Prompt. The resolve beat passes a multi-line posting as one argument, which
PowerShell can do and `cmd.exe` can't. Set up the session once:

```powershell
cd C:\Users\scisp\ClaudeCodeWork\jscc
$env:PATH = "$env:USERPROFILE\.local\bin;$env:PATH"
chcp 65001
```

`chcp 65001` keeps em dashes intact when a case is copied to the clipboard
(take 3a). Run `cls` between takes.

**Before recording:** reset the fixture so the run is reproducible and matches
this script's output exactly.

```powershell
Remove-Item data\synthetic.db -ErrorAction SilentlyContinue
uv run jscc db init
uv run jscc seed --random-seed 42 --now 2026-08-28T12:00:00+00:00
```

**Profile for the recording:** `resolve_profile_path` prefers a private
profile over the tracked example in every mode, so if `config/profile.private.yaml`
exists on the recording machine, scoring and drafting will use it —
real comp target, real must-haves, real writing samples, on camera. Before
recording, either delete/move that file so synthetic mode falls back to
`profile.example.yaml`, or copy the redacted stand-in built for this purpose:

```powershell
Copy-Item docs\demo-fixtures\profile.demo.yaml config\profile.private.yaml
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
- **Take structure:** record one take per scene within each beat (the lettered
  takes below), not the whole script in one go. A bad take then costs 15–40
  seconds, not the full run.
- **Alternatives, if OBS is too much setup:** Windows Snipping Tool
  (Win+Shift+R) is built in and fine for one clean take per section, but has
  no scene switching or separate audio track. Loom is easy but uploads to
  their cloud; keep this footage local.
- **Editing:** Clipchamp (built into Windows 11) or DaVinci Resolve (free)
  covers trims and the edit notes at the bottom.

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

Set up five scenes, each with a hotkey. **One scene is one window, and every
take is one scene.** The terminal and the browsers are never on screen
together, and nothing switches scene mid-recording. A beat that changes scene
is split into lettered takes (3a, 3b), each tagged with its scene. For each
take: pick the scene (hotkey), start recording (`Ctrl+Alt+R`), do the take,
stop (`Ctrl+Alt+S`). Join the clips in the edit.

Window Capture records its window even when something else is on top of it,
so you can leave the terminal, editor, and browser open and let the scene
decide which one the viewer sees. A *minimized* window records as a frozen or
blank frame, so don't minimize any of them.

| Scene | Source | Used for |
|---|---|---|
| **TERM** | Window capture: terminal only | Every CLI command |
| **DOCS** | Window capture: editor showing README / `evals/README.md` | README, status table, eval round tables, architecture diagram |
| **BROWSER** | Window capture: browser at ~1280×800 | `jscc serve` dashboard |
| **CHAT** | Window capture: a *second* browser window, claude.ai only | The live model answer (take 3b) |
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
- **CHAT** needs its own browser window, separate from the dashboard's, so the
  two scenes never share a window. In it, use claude.ai's **Incognito chat**
  (the ghost icon), not a browser incognito window, which would put a sign-in
  on camera. Collapse the sidebar so your name and chat history aren't
  visible, and add a Crop/Pad filter on the source to trim any account menu
  left in a corner.
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
| Switch to scene CHAT | `Ctrl+Alt+5` |
| Start Recording | `Ctrl+Alt+R` |
| Stop Recording | `Ctrl+Alt+S` |

Start and stop on a keypress instead of clicking the OBS window means the
click and the OBS window never touch the footage. Do a 10-second test take of
each scene and play it back before the first real one: check that each clip
shows only its own window, the text is readable at full screen, and track 2
holds the mic. Trim the first second or two of each clip in the edit; it
holds the hotkey press.

---

## 0:00–0:20 — Open

**Takes:** 1 — DOCS.

**Show:** README.md at the top, scrolled to the one-line pitch and the
"Start here: six things worth reading first" list. Don't scroll further.

**Say:** "This is JSCC — a job search pipeline tracker with two LLM stages:
extraction and fit scoring, plus a follow-up drafter that only drafts the
routine cases and hands the rest to a person. Everything you're about to see
is a real command against a synthetic fixture, and where it needs a model's
answer, a real one — nothing staged, nothing faked."

---

## 0:20–1:10 — Ingest, and the DLQ recovery path

**Takes:** 2 — TERM (whole beat).

**Show:** Terminal. Run:

```powershell
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

```powershell
uv run jscc resolve-dlq <entry-id> --paste-text (Get-Content docs\demo-fixtures\jd-posting-hybridized.txt -Raw) --company "Vireo Systems" --title "Senior Engineering Manager, ML Platform & Trust Infrastructure"
```

`--title` and `--company` are both given on purpose. Without them the stub
extractor can't verify the posting's title, and the command stops to ask
"Title [(untitled)]:" on camera.

**Say:** "The recovery path is the same one line the dashboard's resolve form
calls — paste the text by hand, and it goes through the same extraction and
storage path a clean fetch would have. Nothing's lost, and there's no second
code path to keep in sync."

**Show:** `uv run jscc dlq list` again — the entry is gone from the
unresolved list.

---

## 1:10–2:20 — Score it, live, through a real chat

This beat replaces the stub-client commands. There is no API key for this
project, so `jscc score` and `jscc followup` would only print a "no live
scoring" placeholder, which isn't worth putting on screen. Instead the beat
shows the method the project actually uses: print an eval case's exact prompt,
paste it into a Claude.ai chat, read the real answer, then show the harness
that replays those captured answers.

**Do not run `--manual` or `capture_tools.py next` on camera.** Both write to
`evals/fit_scoring/recorded.json`, and this case's prompt hash matches the
committed recording, so a take would overwrite real captured data. The `show`
command below only prints.

**Prepare off camera (before take 3a):** run
`uv run python scripts\capture_tools.py prompts fit_scoring` once so the case
files are current. Open a fresh claude.ai **Incognito chat** in the CHAT
window and select the model the case names, **Sonnet 5** (the printed prompt
states it first and last; the claude.ai default has moved on, so pick it
explicitly). The case content is fictional and the profile is the tracked
example profile, so nothing personal is in the paste.

**Takes:** 3a — TERM (show the prompt). 3b — CHAT (paste, send, read the
answer). 3c — TERM (the replayed result). 3d — DOCS (the round table).

**Take 3a — Show:** Terminal.

```powershell
uv run python scripts\capture_tools.py show fit_scoring 4
```

Let the output scroll, then scroll back to the `--- USER MESSAGE ---` heading
so the job posting and profile are on screen. Stop the take, then, off camera,
copy the case to the clipboard:

```powershell
uv run python scripts\capture_tools.py show fit_scoring 4 | clip
```

**Say:** "Here's how I validate the model stages without an API key. This is
one scoring case: the exact system prompt and the exact message the tool would
send. It's a posting with a deal-breaker in it, against a synthetic profile."

**Take 3b — Show:** CHAT scene, empty incognito chat. Paste (`Ctrl+V`), send,
and let the answer render. Cut the wait in the edit. Hold on the finished
answer: a `score` number and a one-to-three sentence `rationale`. Before you
send, check that the em dashes in the pasted prompt aren't mangled; if they
are, rerun the `| clip` step after `chcp 65001`.

**Say:** "I paste it into a fresh Claude chat, on the same model the case
names. That's a real model answer, not a stub. A deal-breaker should pull the
score low, and the rationale should name it."

(The answer won't match the recorded one word for word. That's fine, and it's
the point of the variance disclosure later: this harness's pass rate moves a
few points between captures.)

**Take 3c — Show:** Terminal.

```powershell
uv run jscc eval fit_scoring --replay | findstr /C:"passed" /C:"case-04"
```

Five lines print: the `29/30 passed (97%)` summary, three per-group lines
(core, hostile, hostile held-out), and `[PASS] case-04` last. Hold on the
summary, then the `[PASS]` line.

**Say:** "I did that for every case, saved each answer, and the harness
replays them against today's grading code. Twenty-nine of thirty pass. Same
caveat as everything here: the prompt was tuned against these cases, so it's
not a held-out number."

**Take 3d — Show:** DOCS, `evals/README.md`, the `fit_scoring` row of the
results table (two rounds of 27/28 on the revised prompt, 64% on the first
prompt).

**Say:** "The first prompt scored 64 percent. The rounds that missed the bar
are in the repo too."

---

## 2:20–2:50 — The drafter

**Takes:** 4 — DOCS (whole beat).

The drafter's real behavior comes from the captured routing and composition
answers, reproduced verbatim in the README. `jscc followup` is deliberately
not run on camera: without a key it calls a stub that answers "handle
manually," which says nothing about the design.

**Show:** README's "Sample drafter output" section — the routine draft next
to the non-routine briefing card, side by side if the editor supports a
split view.

**Say:** "The drafter routes first and only drafts the routine cases. There's
a command for this, `followup`, and with a key it calls the model. Without one
it can only run a stub, so here's the real captured output instead. A full-loop
onsite thank-you gets drafted. A candidate deciding to withdraw gets a
briefing card — no draft, because that reply affects a relationship and
deserves a person's judgment, not an autocomplete."

---

## 2:50–3:40 — The eval harness

**Takes:** 5a — TERM (the `eval` command, the summary line, and the first
**Say**). 5b — DOCS (the round-by-round table and its **Say**).

**Show:** Terminal.

```powershell
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

## 3:40–4:10 — Cost and instrumentation

**Takes:** 6 — TERM (whole beat).

**Show:**

```powershell
uv run jscc costs
```

**Say:** "Every call to a model goes through one path, and that path is
metered — tokens, latency, dollars, all captured at the call site. No real
dollar figures exist yet, because every call so far has been a stub or a
hand-captured chat, never a billed API request. What's built and tested is
the machinery — the honest claim today is that cost tracking works, not what
this costs to run."

---

## 4:10–4:40 — The dashboard

**Takes:** 7a — TERM (run `uv run jscc serve` and hold on the "Uvicorn
running" line for a second; leave the server running afterward, since it
holds the terminal). 7b — BROWSER (everything below, starting with the page
already open at `http://127.0.0.1:8000`; open it before you start the take so
no address-bar typing is on camera).

**Show:** Click through: the home page (Funnel, Pipeline, and Stale alerts are sections of
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

## 4:40–5:00 — Close

**Takes:** 8a — DOCS (the architecture **Show** and the whole **Say**). 8b —
END (hold the end card 3–5 seconds, no narration; lay the closing sentence
over it in the edit if you want it spoken on the card).

**Show:** README's "Architecture" section (the mermaid diagram) or the ADR
list, whichever renders more cleanly on screen.

**Say:** "Seven ADRs, ten design principles, and two cold review passes —
one adversarial, one an outside reviewer's read — at every phase boundary.
The repo's linked in the description if you want to see how any of this
actually works."

**Show:** End card with the repo URL (take 8b, scene END).

---

## Notes for the edit

- If `jscc serve` output looks empty or wrong on the recording machine, rerun
  the seed reset above — the dashboard reads the same `data/synthetic.db`
  the CLI commands populate.
- The live chat answer (take 3b) won't match the recorded one word for word.
  If it comes back with a clearly wrong score, retake it: a bad take is 15–40
  seconds, and the replayed 29/30 in take 3c is the number the repo stands on.
- `jscc score` and `jscc followup` aren't run on camera, since without an API
  key they print a stub placeholder. The narration in beats 3 and 4 already
  says so.
- Don't record over a `JSCC_DATA=real` session by habit — check the mode
  banner / `[mode: ...]` prefix before pressing record, not after.
