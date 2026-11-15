# LightClip — Product Requirements Document (PRD)

| | |
|---|---|
| **Product name (working title)** | LightClip |
| **Document version** | v2.0.0 — Effortless release |
| **Date** | November 12, 2026 |
| **Author** | AutoClaw, on behalf of the product owner |
| **Status** | v1.0.1 shipped; v2.0 web UI in progress |
| **Companion document** | `LightClip-Technical-Design.md` |

---

## 1. Product Overview

LightClip is a **free, local-first, API-powered command-line tool** that turns one long video (podcast, talk, stream VOD, lecture, interview) into multiple **ready-to-post vertical short clips** (YouTube Shorts / TikTok / Instagram Reels) with:

- automatic highlight selection (via the user's own Claude or OpenAI API key — "BYOK"),
- 9:16 reframing (center crop, blur-pillarbox, or face-tracked crop),
- burned-in animated ("pop-in") captions generated from word-level timestamps,
- per-clip titles, descriptions, and hashtags.

It runs entirely on the user's own machine, is engineered for **modest hardware (8 GB RAM, no GPU, Intel i3 12th-gen)**, and produces output quality intended to match the paid cloud tools (OpusClip, Klap, Vizard, Choppity, ClipKitty, SupaClip) at **near-zero recurring cost**.

**One-line pitch:** *The $0, watermark-free OpusClip replacement that runs on a budget laptop.*

---

## 2. Problem Statement

Creators who want to repurpose long videos into shorts currently face a tradeoff:

1. **Paid cloud tools** (OpusClip ~$15–29/mo, Vizard $29/mo, Klap ~$29/mo) have usable but capped free tiers — watermarked output, minute limits (60 min/month typical), 720p/10-minute export caps — and require uploading the video to third-party servers.
2. **Self-hosted alternatives** exist (SupaClip, ClipVideo, etc.) but typically assume GPU hardware, Docker infrastructure, or heavy PyTorch installations that are impractical on a budget laptop.
3. **Manual editing** in a desktop editor takes 2–4 hours per long video.

**The gap:** there is no tool that delivers paid-tool output quality, free of watermarks and subscriptions, while running comfortably on an 8 GB RAM / no-GPU laptop and keeping the media on the user's own machine.

---

## 3. Goals

| # | Goal | Measure |
|---|---|---|
| G1 | Output quality parity with paid tools | Captions, crop, and clip choice indistinguishable from OpusClip-class output in blind spot-checks |
| G2 | Runs on modest hardware | Peak RAM ≤ 2.5 GB; no GPU; 1-hour source → finished clips in ≤ 30 min on i3-12th-gen |
| G3 | BYOK intelligence | Works with the user's existing Claude **or** OpenAI API key; no third-party account besides the LLM provider |
| G4 | Near-zero recurring cost | API cost ≤ $0.50 per hour of source video; no subscriptions; no watermarks |
| G5 | Dead-simple UX | One command with sensible defaults: `lightclip input.mp4` → output folder of finished clips |
| G6 | Privacy | Media never leaves the machine; only the extracted **text transcript** is sent to the LLM API |

---

## 4. Non-Goals (v1)

- ~~No GUI~~ — CLI-only was a v1 non-goal; the local web UI shipped in v2.0 (`--ui`). What remains a non-goal: timeline-style manual *editing* inside the GUI.
- No auto-posting / scheduling to platforms.
- No video *generation* or effects beyond reframing and captions.
- No timeline-style manual editing.
- No model training or fine-tuning.
- No multi-user / server deployment — strictly a single-user local tool.

---

## 5. Background & Competitive Landscape

| Tool | Model | Free tier reality | Catch |
|---|---|---|---|
| OpusClip | Freemium | 60 min/month, free forever | Watermark, no editing, no re-render control; paid from ~$15/mo (150 min) |
| Vizard | Freemium | 60 credits/month | 720p export, 10-min max export, 3-day storage; $29/mo |
| Klap | Paid | Small trial | Plans count clips, not minutes; ~$29/mo |
| ClipKitty / Choppity | Freemium | Trial credits | Same pattern; ~$20–30/mo |
| SupaClip | Open source (self-host) | Free | Aimed at streamers; assumes self-hosting capability |
| **LightClip** | **Local + BYOK** | **Unlimited** | **No watermark; media stays local; requires terminal use** |

**Positioning statement:** For creators on budget hardware who refuse subscriptions and watermarks, LightClip delivers the same transcription → selection → cut → caption pipeline the paid services run in their datacenters — locally, with the user's own LLM key doing the "smart" part.

---

## 6. Target Users & Personas

**P1 — The solo podcaster (primary beneficiary)**
Records 45–90 min episodes; wants 5–10 shorts per episode to feed daily posting; has zero editing skills and zero editing budget.

**P2 — The streamer / YouTuber**
Has long VODs and talks; wants highlight moments surfaced automatically with captions and vertical crop.

**P3 — The educator / speaker**
Turns lectures, webinars, and conference talks into shareable lesson snippets.

**P4 — The hobbyist builder (the original requester)**
Technically curious, has an i3-12th-gen / 8 GB / no-GPU laptop, owns Claude/OpenAI API keys, wants a tool they understand module-by-module and can extend.

*Primary persona for v1: P4 (builder-user), with P1 as the first "real" user to hand the tool to.*

---

## 7. Use Cases & User Stories

| ID | User story | Priority |
|---|---|---|
| UC-01 | As a user, I give a **local video file** and get N finished vertical clips in a folder. | P0 |
| UC-02 | As a user, I control **how many clips** and their **min/max duration**. | P0 |
| UC-03 | As a user, I choose a **platform preset** (Shorts / TikTok / Reels) that sets aspect ratio and duration limits. | P1 |
| UC-04 | As a user, I can pick the **reframe style**: center crop, blur-pillarbox, or face-tracked. | P0 (center/blur), P1 (face-track) |
| UC-05 | As a user, I can toggle **captions on/off** and change caption styling. | P0 (on/off), P1 (styling) |
| UC-06 | As a user, I get a **manifest** of clips with title, description, hashtags, and virality score for copy-pasting into platform upload forms. | P0 |
| UC-07 | As a user with a **video URL** (YouTube, etc.), I can paste the URL and have the video fetched via yt-dlp. | shipped in v0.1 |
| UC-08 | As a user, I can **re-run selection without re-transcribing** (transcript is cached), e.g. to get 12 clips instead of 8. | P1 |
| UC-09 | As a user, I can run a **fast draft** (keyframe-snapped cuts, no re-encode) to preview clip choices quickly. | P1 |
| UC-10 | As a user, I can process a **video in another language** (auto-detected or forced). | P1 |

---

## 8. Functional Requirements

Priority key: **P0** = must-have for v0.1 · **P1** = v0.2 · **P2** = later.

### 8.1 Inputs & Media Handling

| ID | Requirement | Pri |
|---|---|---|
| FR-1 | Accept local video files: `.mp4`, `.mkv`, `.mov`, `.webm`, `.avi`, `.ts` | P0 |
| FR-2 | Accept video URLs via yt-dlp integration | P0 (shipped v0.1) |
| FR-3 | Probe and report source properties (resolution, fps, duration, audio presence) before processing | P0 |
| FR-4 | Refuse or degrade gracefully on videos without an audio track | P0 |

### 8.2 Transcription

| ID | Requirement | Pri |
|---|---|---|
| FR-5 | Transcribe with faster-whisper producing **word-level timestamps** | P0 |
| FR-6 | Model selectable: `tiny`, `base`, `small`, `medium`; default `small` int8 | P0 |
| FR-7 | Language: auto-detect by default; forceable via flag | P1 |
| FR-8 | Cache the transcript keyed by file hash so re-runs skip transcription | P1 |
| FR-9 | Voice-activity filtering (VAD) to skip silence and reduce hallucinated text | P1 |

### 8.3 Highlight Selection (LLM)

| ID | Requirement | Pri |
|---|---|---|
| FR-10 | Provider abstraction supporting **Anthropic Claude** and **OpenAI**; user brings their own key | P0 |
| FR-11 | Model configurable; recommended defaults documented (e.g. Claude Sonnet-class or GPT-4o-mini-class) | P0 |
| FR-12 | LLM returns structured JSON per clip: start, end, title, hook line, caption/description, hashtags, virality score (0–100), one-line reason | P0 |
| FR-13 | Enforce user constraints: clip count N, min/max duration, **non-overlapping** windows | P0 |
| FR-14 | Snap clip boundaries to **sentence/pause boundaries** (never mid-word); tolerate LLM timestamp drift | P0 |
| FR-15 | Chunk long transcripts (map-reduce over ~20–25k-token windows) for 1+ hour videos | P1 |
| FR-16 | Heuristic fallback when no API key is set or the API fails: speech-density × word-rate-variance selection | shipped v0.1 (`--heuristic`) |
| FR-17 | `--dry-run` mode: run selection and print the clip list without rendering | P1 |

### 8.4 Media Processing

| ID | Requirement | Pri |
|---|---|---|
| FR-18 | **Quality mode** (default): frame-accurate cut + re-encode of each clip window only (never the whole video) | P0 |
| FR-19 | **Fast mode**: stream-copy cuts snapped to keyframes for near-instant drafts | shipped v0.2 (`--fast`) |
| FR-20 | Output aspect ratios: 9:16 default; 1:1 and 16:9 selectable | P0 |
| FR-21 | Reframe styles: **center crop** (default), **blur-pillarbox**, **face-tracked crop** (YuNet static smart-crop) | all shipped |
| FR-22 | Burned-in captions from word timestamps with pop-in animation; grouped 2–4 words per caption card | P0 |
| FR-23 | Caption styling configurable: font, size, vertical position, colors, max words per card | shipped v0.2 (`--caption-*`) |
| FR-24 | Optional un-burned `.srt` sidecar per clip | shipped v0.1 (`--srt`) |
| FR-25 | Output H.264 + AAC, `yuv420p`, faststart — guaranteed phone-compatible playback | P0 |

### 8.5 Outputs & Metadata

| ID | Requirement | Pri |
|---|---|---|
| FR-26 | Output folder (`shorts/` by default) containing `clip_01…clip_N.mp4` | P0 |
| FR-27 | `manifest.json` with full metadata for every clip (times, title, caption, hashtags, score, reason, style settings) | P0 |
| FR-28 | Per-clip `.txt` with title, description, hashtags ready to paste into upload forms | P0 |
| FR-29 | Platform presets: Shorts (≤60s default), TikTok, Reels — set ratio + duration caps | P1 |

### 8.6 CLI & UX

| ID | Requirement | Pri |
|---|---|---|
| FR-30 | Single entry command with sensible defaults: `lightclip video.mp4` | P0 |
| FR-31 | Per-stage progress display with elapsed/ETA (extract → transcribe → select → render) | P0 |
| FR-32 | Clear, actionable error messages (missing key, unreadable file, no speech found, etc.) | P0 |
| FR-33 | Exit codes: 0 success, non-zero per failure class (machine-readable) | P1 |
| FR-34 | `--workdir` and cleanup option for intermediate files | P1 |
| FR-35 | Batch mode: `lightclip.py <folder>` processes every video inside (non-recursive, one settings set, per-video output subfolder `<outdir>/<stem>/`, failures don't stop the batch) | shipped v1.0 |
| FR-36 | One-command onboarding: `--setup` wizard persists provider/key/base-url/model as user-scope env vars; `setup.bat` installs Python + ffmpeg + deps on a fresh machine | shipped v1.0.1 |
| FR-37 | Local web UI: `--ui` serves a single-page app on 127.0.0.1 — upload/path/URL sources, settings, live stage progress, results gallery with playback + download | shipped v2.0 |
| FR-38 | Drag-drop upload with progress; raw-body streaming to `work/uploads/` (multipart dropped: `cgi` removed in Python 3.13) | shipped v2.0 |
| FR-39 | Job model: one CLI subprocess per job, stdout parsed into stage/clip progress; page refresh re-attaches to the running job; localhost-only bind + `/files` allowlist | shipped v2.0 |

---

## 9. Non-Functional Requirements

| ID | Requirement | Target |
|---|---|---|
| NFR-1 | Peak memory | ≤ 2.5 GB total process tree (measured: 0.62 GB peak — see BENCHMARKS.md) |
| NFR-2 | Throughput | 1-hour source → finished clips ≤ 30 min wall on i3-12th-gen with `base`/`tiny` (measured: `tiny` ≈ 5 min, `small` ≈ 55 min on U-class — see BENCHMARKS.md) |
| NFR-3 | API cost | ≤ $0.50 per hour of source video at default settings |
| NFR-4 | Hardware | No GPU required at any stage |
| NFR-5 | Platform | Windows 10/11 first-class; macOS/Linux compatible by design |
| NFR-6 | Privacy | Video/audio never uploaded; only transcript text is sent to the configured LLM API |
| NFR-7 | Reliability | Crash-safe: cached stages (transcript, selection) allow resumption without redoing work |
| NFR-8 | Determinism | Re-runs reuse the cached stage outputs (transcript.json, clips.json) so identical inputs give identical outputs; deleting the cache re-rolls LLM selection (temperature 0.3) |
| NFR-9 | Installability | Working setup in ≤ 5 minutes: ffmpeg install + one `pip install` line |
| NFR-10 | Output quality | 1080×1920 when source permits (upscale never); legible captions on a 6" screen |
| NFR-11 | Footprint | ≤ 6 Python dependencies; no PyTorch; single-file CLI entrypoint |

---

## 10. Hardware & Resource Budget

Reference machine: **Intel i3 12th-gen laptop, 8 GB RAM, no GPU, Windows.**

| Stage | Tool | Est. RAM | Est. time (1-hr video) |
|---|---|---|---|
| Audio extraction | ffmpeg | ~300 MB | ~1 min |
| Transcription | faster-whisper `small` int8, CPU | ~800 MB | ~10–15 min |
| Highlight selection | Claude/GPT API | negligible | seconds |
| Cut + 9:16 reframe + captions | ffmpeg + OpenCV YuNet | ~500 MB | ~10 min for 8 clips |
| **Peak (stages run sequentially)** | | **< 2 GB** | **~25 min total** |

Design constraint inherited from this budget: stages must run **sequentially**, model weights must be released before rendering, and only clip windows are ever re-encoded.

---

## 11. Input / Output Specification

**Input:** one video file path (v0.1). Optional flags for clip count, duration range, ratio, style, language, model.

**Outputs (per run), default folder `./shorts/`:**

```
shorts/
├── clip_01__<title-slug>.mp4        # 1080×1920 H.264/AAC, burned captions
├── clip_02__<title-slug>.mp4
├── ...
├── clip_NN__<title-slug>.mp4
├── clip_01.txt                      # title + description + hashtags
├── clip_02.txt
├── ...
└── manifest.json                    # full machine-readable metadata
```

**`manifest.json` (excerpt):**

```json
{
  "source": "episode-42.mp4",
  "generated_at": "2026-11-12T21:04:00",
  "settings": { "clips": 8, "ratio": "9:16", "style": "center", "model": "claude-sonnet" },
  "clips": [
    {
      "id": 1,
      "start": "00:12:41.20",
      "end": "00:13:24.80",
      "duration": 43.6,
      "title": "The $0 editing stack",
      "hook": "Nobody needs a $29 subscription for this.",
      "caption": "How one laptop replaces a full editing suite…",
      "hashtags": ["#podcasting", "#aitools", "#videoediting"],
      "virality_score": 87,
      "reason": "Strong contrarian hook, self-contained story, clear payoff."
    }
  ]
}
```

---

## 12. CLI Sketch (UX-level; full spec in tech doc)

```
lightclip episode-42.mp4                          # defaults: 8 clips, 20–60s, 9:16, center crop, captions on
lightclip episode-42.mp4 --clips 12 --min 15 --max 45
lightclip episode-42.mp4 --style face --ratio 9:16 --platform tiktok
lightclip episode-42.mp4 --fast                   # keyframe-snapped draft cuts
lightclip episode-42.mp4 --dry-run                # selection only, prints the clip table
lightclip episode-42.mp4 --lang de --model gpt-4o-mini
lightclip episode-42.mp4 --no-captions --srt      # clean clips + sidecar subtitles
```

---

## 13. Acceptance Criteria (v0.1 "definition of done")

| # | Criterion |
|---|---|
| A1 | A 1-hour 1080p podcast produces 8 finished clips with peak RAM < 2.5 GB on the reference machine (measured: PASS — 0.62 GB); ≤ 30 min wall holds with `base`/`tiny` ASR, `small` ≈ 55 min on U-class (see BENCHMARKS.md) |
| A2 | Captions match the spoken words (spot-check 20 words across clips: ≥95% match; timing visibly synced) |
| A3 | No clip starts or ends mid-word; starts snap within 0.5 s of a sentence/pause boundary |
| A4 | Every output plays correctly on a phone (H.264 `yuv420p`, AAC, faststart) |
| A5 | Measured API cost < $0.50 for the 1-hour test video |
| A6 | Blind side-by-side vs. an OpusClip free-tier export: caption quality and crop quality rated equal or better by the owner |
| A7 | Re-running the same command overwrites cleanly and reuses the cached transcript |

---

## 14. Milestones

| Milestone | Scope | Exit test |
|---|---|---|
| **M1 — v0.1 core** | FR-1, FR-3–FR-14, FR-18, FR-20–FR-22 (center crop), FR-25–FR-28, FR-30–FR-32 | A1–A5 pass on one test video |
| **M2 — v0.2 polish** | Face-tracked crop, fast mode, `.srt`, caption styling, platform presets, transcript cache, `--dry-run` | ✅ shipped; A6 (blind vs OpusClip) pending owner review |
| **M3 — v1.0** | URL input, batch folder mode, heuristic fallback, resume, docs & packaging, `--doctor`, NFR-1/2 benchmark | ✅ shipped (see BENCHMARKS.md); UC-07/UC-08 demonstrated |
| **M4 — v2.0 Effortless** | Local web UI (upload/path/URL → live progress → results gallery), stdlib-only server, WORKLOG.md | UI verified in a real browser; a clip rendered end-to-end from the UI |

---

## 15. Risks & Mitigations

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | LLM returns malformed JSON or drifts on timestamps | Medium | Medium | Strict schema validation + one auto-retry with error feedback; boundary snapping to transcript timestamps |
| R2 | Whisper word timestamps inaccurate on music/noisy audio | Medium | Medium | VAD filter; medium model option; snap to segment boundaries not raw words |
| R3 | Keyframe-only cutting misaligns audio in fast mode | Medium | Low | Fast mode is draft-only by design; quality mode re-encodes |
| R4 | Face-tracked crop oscillates/jitters | Medium | Medium | Detection sampling at 4 fps + temporal smoothing (lerp) + static-centroid fallback |
| R5 | i3 thermal throttling during long renders | Low | Low | Sequential stages; `ultrafast`/`veryfast` presets; optional low-priority execution |
| R6 | yt-dlp breaks on platform changes | Medium | Low | Feature is P2 and optional; tool never depends on it for core flow |
| R7 | API keys leaked via config files | Low | High | Keys read from environment variables only; never written to disk by the tool |
| R8 | Multilingual videos produce mixed-language captions | Low | Medium | Language forced per run; transcription language detection logged and surfaced |

---

## 16. Open Questions

1. Final product name ("LightClip" is a working title).
2. Should the LLM also generate a thumbnail suggestion text per clip (v2 candidate)?
3. ~~Batch mode semantics~~ RESOLVED (v1.0): one settings set per folder, non-recursive, per-video output subfolders, failures don't stop the batch.
4. Is Twitch/Kick VOD URL handling worth first-class support (the streamer persona) in v1.0?
5. Publish integrations (YouTube/TikTok upload via API) — in scope for a future product, or permanently out?

---

## 17. Glossary

| Term | Meaning |
|---|---|
| ASR | Automatic speech recognition (here: Whisper-family models) |
| ASS | Advanced SubStation Alpha subtitle format; supports styling & animation |
| BYOK | Bring Your Own Key (user-supplied LLM API key) |
| CRF | Constant Rate Factor — x264 quality knob (lower = better/larger) |
| int8 | 8-bit quantization reducing model RAM ~4× with minor accuracy loss |
| Pillarbox | Side bars beside a horizontal video in a vertical frame (here: blurred copies of the frame) |
| VAD | Voice Activity Detection — strips silence before/during ASR |
| VOD | Video on demand (e.g., a recorded stream) |
| YuNet | Lightweight face detector bundled with OpenCV |
| Manifest | JSON file describing all outputs of one run |

---

*End of PRD. Companion technical decisions, commands, schemas, and code-level design live in `LightClip-Technical-Design.md`.*
