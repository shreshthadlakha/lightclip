# LightClip — Engineering Worklog

> Append-only log: per phase — goal, approach, what was built, **why it was built
> that way**, bugs found/fixed, and verified outcomes. Newest phase first.
> Baseline docs this log tracks: `LightClip-PRD.md`, `LightClip-Technical-Design.md`,
> `BENCHMARKS.md`, `README.md`.

---

## v2.0.0 — "Effortless": local web UI (2026-11-14, in progress)

**Goal.** Make LightClip usable without a terminal — the user's bar: "as simple as
breathing"; the product bar: feel like SupaClip/ClipKitty while staying local and free.

**Approach.** Local web app, zero new dependencies: Python stdlib `http.server`
backend (`lightclip/ui_server.py`) + one self-contained HTML file
(`lightclip/web/index.html`, vanilla JS/CSS, no CDN). Launched with
`python lightclip.py --ui` (or double-click `ui.bat`); binds `127.0.0.1` only and
auto-opens the browser.

**Why this way.**
- *Subprocess job model, not in-process refactor:* the UI spawns
  `python lightclip.py <args>` and parses its stdout ([1/4]…[4/4], `Rendering N
  clips`, `  OK clip NN ->`, `Done ->`, `error:`). Zero refactor of the verified
  pipeline, exact CLI parity, and a crash in the UI can't corrupt a run.
- *Stdlib server:* keeps the NFR-11 dependency budget intact; no build step; works
  offline forever.
- *Upload instead of path-only:* browsers don't expose full file paths, so drag-drop
  uploads stream to `work/uploads/` via raw-body `POST /api/upload` (multipart was
  dropped — `cgi` was removed in Python 3.13; a raw-body + `X-Filename` header
  streams cleanly in 256 KB chunks).
- *localhost-only bind + file allowlist:* `/files/*` serves only under known output
  roots with `os.path.realpath` prefix checks (no `../` traversal); nothing listens
  on the network.

**Built.** Frontend: 3-tab source card (upload w/ drag-drop + progress, path, URL),
settings grid (clips/min/max/style/captions/ASR model/free-offline checkbox/fast),
5-segment stage stepper, 1 s status polling, live log tail, results gallery
(`<video>` playback, score badges, download links). Backend: `/api/info`, `/api/upload`,
`/api/start` (409 when busy), `/api/status`, `/api/results`, `/files/*`, `--ui`/`--port`
CLI flags, `ui.bat` shim. Status/resume: refreshing the page mid-job re-attaches to the
running job.

**Outcome.** *(pending — integration + browser verification this phase)*

---

## v1.0.1 — onboarding + setup layer (2026-11-14)

**Goal.** "Anyone can download it, put their own API key and go."

**Built / why.**
- `--setup` wizard: interactive (or `--setup-*` flags for scripted installs) → persists
  provider/key/base-URL/model as **user-scope env vars via `setx`** — the same store
  `--doctor` inspects. Keys never touch project files (PRD R7 stands).
- `setup.bat`: on a fresh Windows box, installs Python and ffmpeg via winget if missing,
  installs pip deps, runs the wizard + doctor. `lightclip.bat` shim → `lightclip <args>`
  as a command.
- `OPENAI_MODEL` / `ANTHROPIC_MODEL` env overrides in `selector.py`: gateway users can
  set their model once; auto-detected runs no longer 404 on `gpt-4o-mini` (the exact
  failure BazaarLink produced).
- `--doctor` extended: reports key + base URL + model rows separately, so "key set but
  no base URL" is visible instead of mysterious.

**Why persistent env vars instead of a config file:** PRD R7 forbids keys in project
files; the OS user-env is the least-surprise standard place, and it makes every new
terminal "just work".

**Outcome.** Verified live: persistent env sourced from registry → real LLM call
(HTTP 200), 28 s end-to-end; doctor all-green. The user's machine is now permanently
configured (no per-session injection needed).

---

## v1.0.0 — M3: batch mode, doctor, formal benchmarks (2026-11-13/14)

**Goal.** Finish the PRD milestone list: batch processing, install verification,
measured NFR-1/2 evidence.

**Built / why.**
- **Batch mode:** `main()` split into `run_one()` + dispatcher; `lightclip.py <folder>`
  processes every video (non-recursive, one settings set for the folder, output to
  `<outdir>/<stem>/`, per-video settings copies so E-04 relaxations don't leak across
  videos, **failures don't stop the batch**, exit 0 only if all succeed). Why copies:
  E-04 mutates min/max per source duration — shared state would corrupt later videos.
- **`--doctor`:** checks Python ≥3.11, ffmpeg/ffprobe on PATH, faster-whisper/pydantic,
  optional yt-dlp/cv2/psutil, vendored YuNet model, key presence. Why: NFR-9
  ("install-from-zero ≤ 5 min") needs a self-verifying install.
- **Benchmark harness** (`tests/benchmark.py`): generates a 1-hour 1080p fixture
  (looped TTS speech, 4.3 GB, one-time), runs all stages inline with a psutil
  process-tree RSS sampler at ~4 Hz, writes `BENCHMARKS.md`.

**Outcome (measured, honest).**
- **NFR-1 PASS: 0.62 GB peak** — ~4× under the 2.5 GB budget; the "quantize or die,
  no PyTorch, sequential stages" design held.
- **NFR-2 model-tiered: `small` = 53.4 min for 60 min audio (~1.1× realtime) on this
  U-class i3 → misses the 30-min budget**; everything else is noise (extract 12 s,
  select 0 s, render 72 s for 8 clips). Cross-referenced `tiny` datapoint (movie test):
  ~12× realtime → 1 h ≈ 5 min. **Decision:** keep `small` as the quality default,
  amend PRD NFR-2/A1 to be model-tiered with measured numbers, document the
  `--asr-model base/tiny` guidance. Honest docs beat aspirational ones.
- Batch live test: 2/2 ok in 32 s.

---

## v0.2.0 — M2: face-tracked crop, fast drafts, caption styling (2026-11-13)

**Approach.** Three parallel builders for `media.py` / `asr.py`+`selector.py` /
`captions.py` against pinned contracts (models/util written first), CLI + tests by the
orchestrator, then integration.

**Built.**
- `--style face`: YuNet static smart-crop per tech doc §S4 — `reframe.py` samples ~40
  frames across the window, takes the dominant (largest) face's center x, crops a fixed
  column at the **median** (jitter-free by design; dynamic per-frame crop deliberately
  deferred). Coverage <60% or portrait sources → center fallback. Model vendored
  (`face_detection_yunet_2023mar.onnx`, MIT/opencv_zoo, 232 KB).
- `--fast`: keyframe-snapped `-c copy` drafts (`__draft` suffix) — 20 drafts in 21 s.
- Caption styling: `--caption-font/size/pos/color/words` (RGB→ASS BGR conversion tested).

**Why static smart-crop instead of dynamic:** the tech doc's own v0.2 decision — dynamic
crop jitters; a median column is indistinguishable for static shots and never worse for
scrolling viewers. Verified visually on the zoo video: face large and centered where a
naive center crop would have cut it off.

**Bugs found via tests (both real algorithm bugs):**
1. Heuristic starvation: candidates only started at ASR *segment* boundaries — long
   VAD-merged movie segments yielded almost no valid 20–60 s windows (asked 20 → got 9,
   bunched in the last 40 min). Fix: slide candidate windows over **every word
   boundary** (supply now scales with the film, not segment luck).
2. Greedy overlap check compared only against `last_end` — a top-scored window late in
   the timeline silently reduced output to ~1 clip. Fix: check against **all** picked
   intervals (picks are score-ordered, not time-ordered).

**Outcome.** 38/38 tests; face crop + fast mode verified live; PRD FR-19/21/23/24 shipped.

---

## v0.1.x — URL input + gateway hardening (2026-11-13)

**Built.** `fetch.py` (yt-dlp, ≤1080p, cached `work/url_<hash>/source.mp4`), CLI URL
detection, `--provider`/`--llm-model`, fast-fail error classes (HTTP 4xx except 429 →
clean exit 4 instead of tracebacks), empty-selection never cached.

**Why.** The user wanted "I paste a link, the engine downloads it" (PRD FR-2 pulled
forward from P2). The 402-from-Selora incident paid for itself twice: it exposed the
traceback UX bug and the empty-cache poisoning bug (a failed `--min/--max` combo would
poison every rerun until `clips.json` was deleted by hand).

**Outcome.** Verified on a direct-file URL and a real YouTube URL (download → probe →
transcribe → live LLM → render, exit 0). PyAV 19 broke faster-whisper 1.2.x
(`metadata_errors` removed) → `av<19` pin.

---

## v0.1.0 — M1: the core pipeline (2026-11-13)

**Approach.** Contracts first (`models.py`, `util.py` written by the orchestrator),
then three parallel builders for the leaf modules, CLI last, then integration + smoke
test on a generated TTS video. ffmpeg install: winget stalled → direct essentials-zip
download to a tools dir + user PATH.

**Why the architecture holds:** faster-whisper int8 + sequential stages + model release
before render keeps peak RAM at ~0.6 GB; the LLM only ever sees transcript *text*
(privacy boundary); boundary snapping after the LLM makes timestamp drift a code
problem, not a prompting problem; per-source-hash caches make every re-run cheap.

**Fixed during build (each verified by a test):**
- `-ss`/`-to` footgun → `-ss {start} -t {dur}` (input seeking resets timestamps);
- `-vf` cannot follow `-filter_complex` on the same output → single complex graph with
  `ass=` folded in after crop, bare filename + `cwd` to dodge Windows colon escaping;
- added `loudnorm` (paid tools normalize loudness; the draft didn't);
- removed forced `-r 30` (judder on 24/25 fps sources);
- vendored-Python `sys.path` quirk → entry script inserts its own folder;
- relative `--outdir` broke under render cwd → resolved to absolute.

**Outcome.** Full pipeline verified: 23 unit tests + end-to-end smoke (TTS video →
150 words transcribed → 2 clips rendered, decode-clean, caption-sync asserted) +
visual frame QA. First live LLM run reached Selora → 402 (free trial unverified) →
switched to the user's BazaarLink gateway: **first real selection + render succeeded**.

---

## M0 — documentation first (2026-11-12)

**Outcome.** PRD v0.1 (problem, personas, FR-1…34, NFR-1…11, A1…A7, M1–M3, risks
R1–R8) + Technical Design v0.1 (5-stage architecture, per-stage specs, resource
budget) + verbatim conversation transcript. Everything later was built against these
documents and updated as reality landed: two ffmpeg draft bugs were fixed *in the doc*
the same day they were fixed in code, and every shipped/deferred requirement is now
annotated in the PRD tables.

---

## Standing decisions (do not relitigate without new evidence)

1. API does the thinking, CPU does the media; no PyTorch anywhere.
2. Never re-encode more than the 20–60 s clip windows.
3. Deterministic snapping after the LLM; every snap adjustment logged in the manifest.
4. Keys in env only — never in project files (R7).
5. Cache stage outputs keyed by source hash; never cache an empty selection.
6. Fast-fail with actionable messages for 4xx; retry only network/429/5xx.
7. Windows-first (bat shims, setx), POSIX compatible by design.

---

## v2.0.1 — project restructure: build/ + docs/ (2026-11-14)

**Goal.** User request: two clean folders — all build/code in one, all documentation
(PRD, tech design, transcript, README, BENCHMARKS, WORKLOG) in the other.

**What.** `build/` now holds lightclip.py, the lightclip/ package, tests/, the three
.bat shims, requirements.txt and pytest.ini; `docs/` holds the six documents. Runtime
outputs (`shorts*/`, `work*/`) and release zips stay at the project root.

**Why the path surgery matters (three real reference fixes, each would have broken
silently otherwise):**
- `ui_server.py` split ROOT into `ROOT` (build/ — sys.path, index.html, lightclip.py
  path) and `PROJECT` (project root — uploads, `/files` allowlist roots, outdir
  default, relative-source resolution, job cwd). Before: everything hung off one
  constant; after the move the UI would have looked for clips in `build/shorts/`.
- `tests/benchmark.py` writes `BENCHMARKS.md` to `docs/` when the folder exists
  (fallback: build root).
- `tests/smoke_e2e.py` writes its artifacts at the project root so the UI's
  `/files` allowlist keeps serving them.

**Verification.** pytest 40/40 from `build/`; UI server relaunched from `build/`
(`--ui`); `/api/info` + `/api/results` still resolve project-root outputs.

**Housekeeping (same day).** The ten phase-test output pairs (`shorts_*/work_*`) were
consolidated: per-source caches merged into the single default `work/` (hash-keyed, so
all caches — including the 2-h movie transcript — stay valid), regenerable test outputs
and `bench_out` deleted. Runtime data: 538 MB → 295 MB. Root now: `build/`, `docs/`,
`shorts/` (default output), `shorts_movie/` (the Blind Side cut), `work/`, release zips.

**GitHub-readiness (same day, user decision: commit `build/` and distribute via GitHub).**
- The five `LightClip-v*.zip` archives deleted — GitHub's repo download + Releases
  supersede hand-made zips; `.gitignore` added so runtime outputs/caches and any
  future local config never enter the repo.
- `README.md` moved from `docs/` to the repo root (READMEs live next to the code) and
  rewritten to OSS conventions: static badges → tagline → features → quick start →
  CLI reference (collapsed) → configuration → how-it-works → cost → benchmarks →
  roadmap → FAQ → contributing → license → acknowledgments.
- License deliberately left "TBD (MIT recommended)" — the user's call before publishing.
- **Publish prep:** `docs/` copied into `build/docs/` so the commit is self-contained
  (canonical docs stay at the project root — re-copy before each commit if they change);
  README gained a Changelog section linking the worklog (the version badge anchors to it).
- Deep documentation stays in `docs/` (PRD, tech design, benchmarks, transcript,
  worklog) and is linked from the README.

