# LightClip — Technical Design Document

| | |
|---|---|
| **Product name (working title)** | LightClip |
| **Document version** | v1.0 |
| **Date** | November 12, 2026 |
| **Companion document** | `LightClip-PRD.md` (requirements, acceptance criteria) |
| **Reference hardware** | Intel i3 12th-gen laptop, 8 GB RAM, no GPU, Windows 11 |

---

## 1. Design Principles

1. **API does the thinking, CPU does the media.** All "intelligence" (moment selection, titles, captions) goes to the user's Claude/OpenAI key. All media math uses CPU-optimized, quantized tooling. No PyTorch anywhere.
2. **Quantize or die (on 8 GB).** Whisper runs via CTranslate2 (`faster-whisper`) with `compute_type="int8"`. Video is decoded/encoded by ffmpeg only.
3. **Never re-encode more than you must.** Only the 20–60 s clip windows are re-encoded; the source file is never fully re-rendered.
4. **Sequential stages, release memory between them.** Peak budget < 2 GB requires that the ASR model is unloaded before rendering starts.
5. **Privacy boundary.** Bytes of media never leave the machine. The only network payload is the transcript text (and metadata) sent to the LLM provider the user chose.
6. **Cache stage outputs.** Transcript and selection results are cached so iteration (e.g., more clips, different style) never redoes paid or slow work.

---

## 2. System Architecture

```
                       ┌──────────────────────────────────────────────────┐
                       │                    lightclip CLI                 │
                       └───────┬──────────────────────────────────────────┘
                               │ orchestration (sequential)
   ┌───────────┐   ┌───────────▼──────┐   ┌──────────────────┐   ┌─────────────────────┐
   │ input.mp4 │──►│ S1 media probe & │──►│ S2 transcription │──►│ S3 LLM highlight    │
   │ or URL    │   │ audio extraction │   │ faster-whisper   │   │ selection (BYOK)    │
   └───────────┘   │ (ffmpeg)         │   │ int8, CPU        │   │ Claude | OpenAI     │
                   └──────────────────┘   └────────┬─────────┘   └──────────┬──────────┘
                                                   │ transcript.json        │ clips.json
                                                   │ (cached)               │ (cached)
                                                   └────────────┬───────────┘
                                                                │
                                        ┌───────────────────────▼────────────────────────┐
                                        │ S4 render (per clip, one ffmpeg call each)     │
                                        │  cut → 9:16 reframe → ASS caption burn → H.264 │
                                        │  reframe: center | blur-pillarbox | face-track │
                                        │  (face-track uses OpenCV YuNet, 4 fps sampling)│
                                        └───────────────────────┬────────────────────────┘
                                                                │
                                        ┌───────────────────────▼────────────────────────┐
                                        │ S5 outputs: shorts/clip_NN.mp4 + .txt +        │
                                        │    manifest.json (+ optional .srt)             │
                                        └────────────────────────────────────────────────┘
```

**Component responsibilities**

| Component | Responsibility | Technology |
|---|---|---|
| `cli` | Arg parsing, stage orchestration, progress UI, exit codes | Python 3.13 stdlib (`argparse`) or `typer` |
| `media` | Probe, audio extraction, cuts, reframe, caption burn | `ffmpeg` / `ffprobe` via `subprocess` |
| `asr` | Transcript with word timestamps, VAD, language detect | `faster-whisper` (CTranslate2) |
| `selector` | Transcript chunking, prompts, schema validation, snapping | `anthropic` or `openai` SDK + `pydantic` |
| `reframe` | Face-track crop trajectory (optional path) | `opencv-python` (YuNet) |
| `captions` | Word groups → ASS generation | pure Python (no dependency) |

---

## 3. Tech Stack & Rationale

| Choice | Rationale |
|---|---|
| **Python 3.13** | Already present on the reference machine; all deps support it |
| **faster-whisper** (CTranslate2) | 4× faster and ~4× lighter RAM than OpenAI Whisper's PyTorch runtime; int8 quantization on CPU; word-level timestamps built in |
| **ffmpeg** (Gyan build via winget) | Single binary; does extraction, cutting, scaling, blur, subtitle burn, encoding |
| **OpenCV (`opencv-python`)** | YuNet face detector is bundled, tiny (~230 KB ONNX), CPU-cheap at 4 fps sampling |
| **`anthropic` / `openai` SDK** | BYOK provider abstraction; structured JSON output support on both |
| **pydantic** | Schema validation of LLM JSON with helpful retry feedback |
| **No PyTorch** | The single biggest RAM/speed killer on this hardware — deliberately avoided |
| **No database** | `transcript.json` + `clips.json` in a work dir are the only state |

**Dependency list (complete):** `faster-whisper`, `av<19` (pulled in by faster-whisper; PyAV ≥19 removed the `metadata_errors` kwarg faster-whisper 1.2.x uses — pin required), `opencv-python` (optional, face-track only), `anthropic` or `openai` (one), `pydantic`, `yt-dlp` (optional, P2). Everything else is stdlib.

---

## 4. Repository Layout (actual, v2.0.1)

```
LightClip/
├── build/                      # ALL code — run commands from here
│   ├── lightclip.py            # CLI entrypoint (orchestration only)
│   ├── lightclip/              # package: media / asr / selector / captions / reframe /
│   │                           #   fetch / ui_server / models / util + web/index.html (v2 UI)
│   ├── setup.bat               # fresh-machine installer (Python, ffmpeg, deps, key wizard)
│   ├── lightclip.bat / ui.bat  # command shims
│   ├── requirements.txt
│   ├── pytest.ini
│   └── tests/                  # pytest suite + smoke_e2e.py + benchmark.py (NFR-1/2)
├── docs/                       # ALL documentation
│   ├── LightClip-PRD.md
│   ├── LightClip-Technical-Design.md
│   ├── LightClip-conversation-transcript.md
│   ├── README.md
│   ├── BENCHMARKS.md           # generated by build/tests/benchmark.py
│   └── WORKLOG.md              # engineering log: approach, why, outcomes per phase
├── shorts*/ work*/             # runtime outputs and per-source caches (project root)
└── LightClip-v*.zip            # release archives
```

---

## 5. Stage Specifications

### S1 — Probe & Audio Extraction

**Probe** (report to user, decide output resolution):

```
ffprobe -v error -select_streams v:0 -show_entries stream=width,height,r_frame_rate,duration -of json input.mp4
```

**Audio extraction** — 16 kHz mono PCM is the exact input faster-whisper wants; one ffmpeg pass:

```
ffmpeg -y -i input.mp4 -vn -ac 1 -ar 16000 -c:a pcm_s16le work\audio.wav
```

- ~115 MB per hour on disk (acceptable); switch to `-c:a flac` to halve it if needed.
- RAM ~300 MB, ~1 min per hour of video on the reference machine.
- Output resolution policy: never upscale. If source height < 1920, output = source-scaled (e.g., 720p source → 720×1280).

**URL input (FR-2):** http(s) links are fetched by yt-dlp into `work/url_<hash>/source.mp4`
(≤1080p mp4/m4a merge, cached — re-runs skip the download; `FetchError` covers a missing
yt-dlp install and DownloadError), then flow through S1–S5 unchanged. The manifest's
`source` field carries the URL; local file hash caching applies as usual.

### S2 — Transcription

```python
from faster_whisper import WhisperModel

model = WhisperModel("small", device="cpu", compute_type="int8", cpu_threads=6)
segments, info = model.transcribe(
    "work/audio.wav",
    vad_filter=True,            # skip silence; cuts hallucination on dead air
    word_timestamps=True,       # required for pop-in captions
    language=None,              # auto-detect; CLI can force
)
# persist: transcript.json {language, duration, segments:[{start,end,text,words:[{start,end,word}]}]}
```

**Model selection table (CPU int8, reference machine):**

| Model | RAM | 1-hr audio speed | Quality | Use when |
|---|---|---|---|---|
| tiny | ~350 MB | ~1–2 min | rough | quick tests |
| base | ~500 MB | ~2–4 min | ok | clear solo speech |
| **small** | **~800 MB** | **~8–15 min** | **good** | **default** |
| medium | ~2.2 GB | ~25–40 min | best CPU option | heavy accents/multilingual |

`large-v3` is explicitly not recommended on this hardware (RAM + speed). `medium` exceeds the NFR-2 ≤30 min wall-time budget (transcription alone ~25–40 min) — treat it as a quality override, not a default. Release the model (del `model`, `gc.collect()`) before S4.

**Transcript JSON schema (cached as `work/transcript.json`, key = SHA-256 of file):**

```json
{
  "source_hash": "…", "language": "en", "duration": 3612.4,
  "segments": [
    { "start": 761.2, "end": 764.8, "text": "Nobody needs a subscription for this.",
      "words": [ {"start": 761.2, "end": 761.5, "word": "Nobody"}, … ] }
  ]
}
```

### S3 — LLM Highlight Selection (BYOK)

**Chunking (map-reduce, only for transcripts > ~20k tokens):**
- Map: transcript split into chunks of ~20–25k tokens with 1-segment overlap; each chunk scored independently: "return top 3 candidate moments with exact timestamps."
- Reduce: all candidates merged; final prompt picks N non-overlapping winners and writes metadata.

**System prompt (reduce stage, abridged — full text lives in `selector.py`):**

```
You are a short-form video editor. From the transcript with timestamps,
select the {n} most engaging self-contained moments for TikTok/Shorts.

Rules:
- Each clip: {min}–{max} seconds.
- Clip must start at a sentence beginning and end at a sentence end.
- Prefer: strong hooks, contrarian takes, stories with payoffs, concrete numbers,
  emotional moments. Avoid: mid-topic segments, jokes needing setup, filler.
- Timestamps MUST be copied exactly from the transcript — never invent times.
Return ONLY valid JSON matching the provided schema.
```

**Selection schema (enforced by pydantic):**

```json
{
  "clips": [
    { "start_sec": 761.2, "end_sec": 804.8,
      "title": "The $0 editing stack",
      "hook": "Nobody needs a $29 subscription for this.",
      "caption": "How one laptop replaces an entire editing suite.",
      "hashtags": ["#podcasting", "#aitools"],
      "virality_score": 87,
      "reason": "Contrarian hook + concrete payoff, self-contained." }
  ]
}
```

**Provider abstraction:**

| | Claude path | OpenAI path |
|---|---|---|
| Default model | Sonnet-class (`claude-sonnet-4-5`) | `gpt-4o-mini` |
| Key | env `ANTHROPIC_API_KEY` | env `OPENAI_API_KEY` |
| Structured output | tool-use / strict JSON prompt + pydantic validate | `response_format: json_schema` + validate |
| Retries | 3 attempts, exponential backoff; on schema failure, re-send the validation error for self-correction | same |
| Temperature | 0.3 | 0.3 |

**Boundary snapping (post-LLM, deterministic):**
1. For each clip: find nearest segment boundary to `start_sec` (tolerance 0.5 s) → set start to that boundary − 0.3 s pre-roll.
2. Extend/trim `end_sec` to the next segment boundary so the clip ends on a completed sentence.
3. Enforce non-overlap after snapping; drop/re-pick if a clip collapsed below `min`.
4. Log every snap adjustment in the manifest (transparency).

**Fallback heuristic (no key / API down, P2):** rank 20–60 s windows by (speech density × word-rate variance) as a proxy for energy; no titles beyond "Clip N".

**Cost math (1-hr video):** transcript ≈ 9–15k words ≈ 12–20k tokens input; reduce stage output ~2k tokens.
- GPT-4o-mini-class: ≈ $0.005–0.01 per hour of video.
- Sonnet-class: ≈ $0.10–0.25 per hour of video.
Well under the NFR-3 ceiling of $0.50/hr.

### S4 — Render (per clip; N ffmpeg invocations)

**Cut + reframe + burn, center-crop 9:16 (the default single command per clip):**

```
ffmpeg -y -nostdin -ss {start:.3f} -t {dur:.3f} -i input.mp4 -filter_complex ^
"[0:v]scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,crop=1080:1920,ass=captions_clip01.ass[v]; ^
 [0:a]aformat=sample_rates=48000:channel_layouts=stereo,loudnorm=I=-16:TP=-1.5:LRA=11[a]" ^
-map "[v]" -map "[a]" ^
-c:v libx264 -preset veryfast -crf 20 -pix_fmt yuv420p ^
-c:a aac -b:a 128k -movflags +faststart shorts\clip_01.mp4
```

Notes (corrected during the build — the draft command had two bugs):

- `-ss {start}` **before** `-i` = fast seek; the duration must be `-t {dur}` — the draft's `-to {end}` (absolute) is wrong under input seeking, which resets timestamps to 0.
- The `ass` filter lives INSIDE the single `-filter_complex`, after `crop` — `-vf` cannot follow a complex filtergraph on the same output stream (the draft mixed both). The `.ass` file is passed as a bare filename and the render runs with `cwd` = cache dir, avoiding Windows drive-colon escaping in filter args.
- `loudnorm=I=-16:TP=-1.5:LRA=11` (one-pass) normalizes loudness so clips cut from different parts of a video come out consistent.
- No `-r` flag: source fps is preserved (the draft's forced `-r 30` adds judder on 24/25 fps sources).
- Cover-crop order (`scale … increase` → `crop`) keeps geometry correct for any source; output never exceeds 1080×1920.

**Blur-pillarbox variant (for wide shots / two-person framing where center crop cuts people off):**

```
-filter_complex "[0:v]split=2[bg][fg]; ^
 [bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920, ^
     boxblur=luma_radius=20:luma_power=2[bgb]; ^
 [fg]scale=1080:1920:force_original_aspect_ratio=decrease[fgs]; ^
 [bgb][fgs]overlay=(W-w)/2:(H-h)/2[v]"
```

**Face-tracked variant (shipped v0.2, `--style face`):** `reframe.py` samples ~40 frames
across the clip window (cv2.VideoCapture seek + YuNet `FaceDetectorYN`, vendored
`face_detection_yunet_2023mar.onnx`, MIT/opencv_zoo); the dominant (largest) face's
center x per frame → median → **static smart-crop column** (jitter-free), scaled into
the cover-scale frame (s = max(out_w/src_w, out_h/src_h)) and clamped. Coverage <60%
or sources without horizontal slack fall back to center crop (logged in the run).

**Fast mode (draft, P1):** `-ss {start} -to {end} -c copy` — instant, but cuts land on keyframes (up to ~2–5 s drift); UI must label output as "draft".

### S5 — Captions (ASS generation, pure Python)

**Grouping rule:** Whisper words → cards of **2–4 words / ≤ 1.2 s** (never split a word pair across cards). One card = one ASS `Dialogue` event spanning its words' exact timestamps.

**Style spec (TikTok-class "pop" captions):**

| Property | Value |
|---|---|
| Font | Arial Black / Impact (fontfile resolved on Windows) |
| Size | 64 px @ 1080×1920 playfield |
| Primary | white `&H00FFFFFF`; keyword words optionally yellow `&H0000FFFF` |
| Outline / Shadow | 3 px black outline, 0 shadow (legibility on any background) |
| Alignment | `\an5` centered at ~72% frame height (below face zone) |
| Animation | pop-in: `{\fad(60,0)\t(0,110,\fscx115\fscy115)\t(110,180,\fscx100\fscy100)}` |

**ASS excerpt (illustrative):**

```ass
[Script Info]
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, Outline, Alignment
Style: Pop,Arial Black,64,&H00FFFFFF,&H00000000,3,5

[Events]
Format: Start, End, Text
Dialogue: 0,0:12:41.20,0:12:41.85,{\an5\pos(540,1380)\fad(60,0)\t(0,110,\fscx115\fscy115)\t(110,180,\fscx100\fscy100)}NOBODY NEEDS
Dialogue: 0,0:12:41.85,0:12:42.60,{\an5\pos(540,1380)\fad(60,0)\t(0,110,\fscx115\fscy115)\t(110,180,\fscx100\fscy100)}A {\c&H0000FFFF&}SUBSCRIPTION{\c}
```

Burned via `-vf "ass=captions_clip01.ass"` (ASS filter accepts the animation tags above). Optional clean copy (captions off) and `.srt` sidecar come from the same grouped-words data.

### S6 — Outputs

- Naming: `clip_{id:02d}__{title-slug}.mp4` (slug = ASCII-lowercase-hyphenated title, truncated 40 chars).
- `clip_NN.txt`: title / description / hashtags blocks for paste-into-upload.
- `manifest.json`: full metadata incl. snap adjustments and settings (schema in PRD §11).

---

## 6. Resource & Performance Budget (detailed)

| Stage | Sub-step | RAM (est.) | 1-hr video (est., ref. machine) |
|---|---|---|---|
| S1 | probe + audio extract | ~300 MB | ~1 min |
| S2 | faster-whisper small int8 (+ VAD) | ~800 MB | ~10–15 min |
| S3 | LLM API round-trips | negligible | ~10–60 s |
| S4 | per-clip encode (veryfast, 1080×1920) | ~400–600 MB | ~1–1.5 min × 8 clips |
| S4 (face-track) | + YuNet @4 fps | +200 MB | +30 s per clip |
| **Peak (sequential)** | | **< 2 GB** | **~25 min total** |

**Tuning knobs** (if the machine struggles): `ultrafast` preset · `tiny`/`base` ASR model · disable face-track · 720p output cap · `-threads` capped to keep the laptop usable during renders.

---

## 7. Configuration

**Precedence:** CLI flags > environment > `lightclip.toml` (optional) > defaults.

**Environment variables (keys never written to disk by the tool — NFR/FR-R7):**

```
ANTHROPIC_API_KEY=sk-ant-…        # or
OPENAI_API_KEY=sk-…
```

**Config file (optional, all keys optional):**

```toml
[asr]        model = "small"      # tiny|base|small|medium
[selection]  provider = "anthropic"   # anthropic|openai
             model = "claude-sonnet-4-5"
[output]     dir = "shorts"
             ratio = "9:16"           # 9:16|1:1|16:9
             style = "center"         # center|blur|face
             captions = true
[render]     preset = "veryfast"      # ultrafast|veryfast|fast
             crf = 20
```

**CLI reference:**

| Flag | Default | Meaning |
|---|---|---|
| `--clips N` | 8 | number of clips |
| `--min / --max SEC` | 20 / 60 | clip duration bounds |
| `--ratio` | 9:16 | 9:16 / 1:1 / 16:9 |
| `--style` | center | center / blur / face |
| `--platform` | — | shorts / tiktok / reels preset (overrides ratio+durations) |
| `--captions / --no-captions` | on | burn captions |
| `--srt` | off | also write .srt sidecars |
| `--lang` | auto | force ASR language (ISO code) |
| `--asr-model` | small | tiny / base / small / medium |
| `--provider` | auto-detect from env | anthropic / openai |
| `--llm-model` | per provider | model id override |
| `--fast` | off | keyframe-snapped draft cuts (no re-encode) |
| `--doctor` | — | verify install: PATH, Python deps, YuNet model, API key |
| `--ui` / `--port` | off / 8765 | local web UI on 127.0.0.1 (§14) |
| `--caption-font/--caption-size/--caption-pos/--caption-color/--caption-words` | style spec | FR-23 caption styling overrides |
| `--dry-run` | off | selection only; prints clip table |
| `--heuristic` | off | offline speech-density selection — no API key/cost (FR-16) |
| `--outdir / --workdir` | ./shorts / ./work | paths |
| `--keep-work` | off | keep intermediates after success |

---

## 8. Error Handling & Edge Cases

| ID | Case | Detection | Behavior |
|---|---|---|---|
| E-01 | No audio track | ffprobe stream scan | Exit 2 with clear message |
| E-02 | Missing API key | env check before S2 | Offer `--heuristic` (P2) or exit 3 with setup hint |
| E-03 | LLM JSON invalid ×3 | pydantic | Exit 4; cached transcript retained; error text shown |
| E-04 | Video too short (< 2× max duration) | probe | Auto-relax `--min`; warn if < 2 clips possible |
| E-05 | No speech found (music-only, silent) | ASR empty / VAD 100% rejected | Exit 5, explain |
| E-06 | Non-16:9 sources (portrait, 4:3) | probe | Reframe math adapts; never upscale beyond 1080×1920 |
| E-07 | Unicode/emoji in speech & titles | everywhere | UTF-8 throughout; slugify strips non-ASCII for filenames |
| E-08 | ffmpeg missing | PATH check at startup | Print the exact `winget install Gyan.FFmpeg` line |
| E-09 | Crash mid-run | any | Work dir cache → rerun resumes at failed stage |
| E-10 | LLM picks overlapping/too-short clips | snapping pass | Auto-fix; drop & log if unfixable; never emit broken timeline |

---

## 9. Testing & Validation Plan

- **Unit:** word grouping (captions.py) golden-file tests; boundary snapping (synthetic transcript + drifted LLM output); slug/naming; config precedence.
- **Integration:** one bundled 10-min sample video → assert: 3 clips, all within duration bounds, all start/end on boundaries, captions text matches transcript slice, outputs decode (`ffmpeg -v error -i out.mp4 -f null -`).
- **Performance benchmark script:** times + peak RAM (via `psutil` sampling) per stage on a 1-hr input; recorded in repo as `BENCHMARKS.md` (target: PRD NFR-1/2).
- **NFR benchmark:** `tests/benchmark.py` — a 1-hour fixture through all stages with
  psutil process-tree RSS sampling (~4 Hz); writes `BENCHMARKS.md` with the NFR-1/2
  verdicts (peak RAM ≤ 2.5 GB, 1 h → clips ≤ 30 min).
- **Manual QA checklist:** phone playback of 3 clips; caption sync spot-check (20 words); blind compare vs OpusClip free export (PRD A6).

---

## 10. Security & Privacy

- Media bytes never leave the machine; the only outbound payload is transcript text + prompt (S3) to the user-chosen provider endpoint.
- API keys are read from environment variables only; never logged, never persisted.
- Work dir (`audio.wav`, `transcript.json`, `clips.json`) is local cache only; `--keep-work off` deletes it after success.
- No telemetry. yt-dlp (optional) is the only component touching third-party video platforms, and only when the user explicitly passes a URL.

---

## 11. Future Roadmap (beyond v1.0)

1. ~~GUI wrapper (local web UI on `localhost`, same core)~~ — **shipped in v2.0** (`--ui`, §14).
2. B-roll / zoom punch-ins on emphasis words (ffmpeg `zoompan`), keyword-highlight captions (already reserved in the style spec).
3. Publish integrations via official platform APIs ( YouTube/TikTok upload), gated behind explicit user OAuth.
4. Virality feedback loop: import view counts back into the manifest to tune selection prompts.
5. ~~`--heuristic` offline mode hardened into a first-class path (zero-API usage)~~ — **shipped in v1.0**.

---

## 12. Appendix A — One-time Setup (reference machine)

```powershell
winget install Gyan.FFmpeg            # ffmpeg + ffprobe on PATH (new terminal after)
pip install -r requirements.txt       # pins av<19 (PyAV 19 breaks faster-whisper 1.2.x)
python lightclip.py episode-42.mp4    # first run downloads the ~460 MB small-int8 model once
```

## 13. Appendix B — Example End-to-End Session

```
> python lightclip.py episode-42.mp4 --clips 8

[1/4] Probing source… 1920×1080 @29.97, 61:24, audio ✓
[2/4] Extracting audio… done (0.9 min)
[3/4] Transcribing (small/int8, lang=en)… 9,412 words  (12.4 min)
[4/4] Selecting highlights (claude-sonnet-4-5)… 8 clips, $0.11
      ┌ clip 01 12:41–13:24  "The $0 editing stack"        score 87
      ├ clip 02 31:02–31:44  "Why nobody finishes courses" score 81
      └ …
      Rendering 8 clips… ████████░░ 8/8 (9.8 min)

Done → shorts/  (8 clips, manifest.json, 8 .txt)   Total 23.1 min · peak RAM 1.7 GB
```

*End of Technical Design Document.*

## 14. v2 — Local Web UI (shipped, `--ui`)

**Architecture: zero-dependency local web app.** `lightclip/ui_server.py` (stdlib only:
`http.server.ThreadingHTTPServer` + threads) + `lightclip/web/index.html` (one
self-contained vanilla-JS page, no frameworks/CDN — works offline). Launched via
`python lightclip.py --ui [--port 8765]` or `ui.bat`; opens the browser automatically.

**Security model:** binds `127.0.0.1` only; `/files/*` serves only under realpath-
allowlisted output roots (no traversal); the UI is served fresh from disk each request
(hot-reload); keys stay in env and are never exposed through endpoints.

**Job model (why subprocess):** one job at a time; `/api/start` spawns
`python lightclip.py <args>` with inherited env (persistent gateway vars apply) and a
daemon thread parses stdout into progress — `\[1/4]…\[4/4]` stage markers,
`Rendering N clips` + clips_total, `  OK clip NN` + clips_done, `Done -> <dir>` → outdir,
`error:` → error. Zero refactor of the verified pipeline; page refresh re-attaches via
`/api/status`. Polling at 1 s from the page; no websockets needed.

**Routes:** `GET /` (index.html) · `GET /api/info` (version, key/base/model presence,
busy) · `POST /api/upload` (raw body + `X-Filename` header, streamed 256 KB chunks to
`work/uploads/` — multipart dropped because `cgi` was removed in Python 3.13) ·
`POST /api/start` (409 when busy, 400 when a non-URL source doesn't exist) ·
`GET /api/status` · `GET /api/results` (manifest.json + mp4 listing with playable
`/files/` URLs) · `GET /files/<relpath>` · `GET /favicon.ico` → 204.
