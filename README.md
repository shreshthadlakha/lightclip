<div align="center">

# 🎬 LightClip

**Turn one long video into ready-to-post vertical shorts — free, local, unlimited.**

[![Version](https://img.shields.io/badge/version-2.0.1-blue)](#-changelog)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)](#-installation)
[![GPU](https://img.shields.io/badge/GPU-not%20required-success)](#-how-it-works)
[![License](https://img.shields.io/badge/license-TBD%20(MIT%20recommended)-orange)](#-license)

*The $0, watermark-free alternative to OpusClip / SupaClip / ClipKitty —
powered by your own API key, running entirely on your machine.*

`Upload or paste a link → AI picks the best moments → vertical clips with pop-in captions → post them`

</div>

---

## ✨ Features

- **One command, many clips** — a 2-hour podcast or movie becomes 8–50 ready-to-post shorts
- **Web UI included** — drag & drop in your browser (`--ui`), no terminal skills needed after install
- **Your key, your choice** — works with Anthropic, OpenAI, or any OpenAI-compatible gateway
- **Zero-key mode** — `--heuristic` selects moments locally; clipping works with **no API key at all**
- **TikTok-class captions** — word-level timestamps, pop-in animation, fully stylable
- **Smart reframing** — center crop, blur-fill, or face-tracked crop (YuNet)
- **Links, not just files** — paste a YouTube URL; the video is fetched automatically (yt-dlp)
- **Batch mode** — point at a folder, get clips for every video inside
- **Private** — media never leaves your machine; only transcript *text* reaches your chosen provider
- **No watermark, no subscription, no minute caps** — the only cost is your own API usage (pennies)
- **Lightweight** — ~0.6 GB peak RAM, no GPU, no PyTorch; runs on an 8 GB laptop

## 🚀 Quick start

**Windows — one click:**

1. Download/clone this repo and double-click **`setup.bat`** — it installs Python and
   ffmpeg if missing, installs the Python dependencies, and walks you through your
   API key once (persisted; never asked again).
2. Open the web UI:

```bat
ui.bat
```

3. Drag in a video (or paste a YouTube URL) → **START** → download your shorts.

**Manual install (any OS):**

```bash
# 1. ffmpeg on PATH
winget install Gyan.FFmpeg          # Windows
# brew install ffmpeg               # macOS

# 2. Python dependencies (3.11+)
pip install -r requirements.txt

# 3. Your LLM key (or skip — see "What it costs")
setx OPENAI_API_KEY "sk-..."        # or ANTHROPIC_API_KEY

# 4. Clip
python lightclip.py your-video.mp4
```

Verify everything with:

```bash
python lightclip.py --doctor
```

## 🖥️ Web UI

```bash
python lightclip.py --ui
```

Opens `http://127.0.0.1:8765` — drag in a video (or paste a path/URL), pick settings,
watch live stage progress, play and download the finished clips in the page.
Runs on `127.0.0.1` only; nothing is exposed to your network.

## ⌨️ CLI usage

```bash
python lightclip.py episode-42.mp4                          # 8 clips, 20–60 s, 9:16, captions on
python lightclip.py episode-42.mp4 --clips 12 --min 15 --max 45
python lightclip.py episode-42.mp4 --style face             # face-tracked crop
python lightclip.py episode-42.mp4 --asr-model tiny         # ~12× realtime transcription
python lightclip.py episode-42.mp4 --heuristic              # NO API key needed
python lightclip.py episode-42.mp4 --fast                   # instant draft cuts (no re-encode)
python lightclip.py "https://youtu.be/..."                  # URL input (yt-dlp)
python lightclip.py ./videos-folder                         # batch: every video inside
python lightclip.py episode-42.mp4 --dry-run                # selection only, prints the table
```

<details>
<summary><strong>All flags</strong></summary>

| Flag | Default | Meaning |
|---|---|---|
| `--clips N` | 8 | number of clips |
| `--min / --max SEC` | 20 / 60 | clip duration bounds |
| `--ratio` | 9:16 | 9:16 / 1:1 / 16:9 |
| `--style` | center | center / blur / face (YuNet smart-crop) |
| `--platform` | — | shorts / tiktok / reels preset |
| `--captions / --no-captions` | on | burn pop-in captions |
| `--caption-font / --caption-size / --caption-pos / --caption-color / --caption-words` | style spec | caption styling overrides |
| `--srt` | off | also write `.srt` sidecars |
| `--lang` | auto | force ASR language (ISO code) |
| `--asr-model` | small | tiny / base / small / medium |
| `--provider` | auto | anthropic / openai |
| `--llm-model` | per provider / `OPENAI_MODEL` | model id override |
| `--heuristic` | off | offline selection — no API key, no cost |
| `--fast` | off | keyframe-snapped draft cuts (no re-encode) |
| `--dry-run` | off | selection only, prints the table |
| `--outdir / --workdir` | ./shorts / ./work | output & cache paths |
| `--keep-work` | off | keep intermediates (audio.wav) |
| `--ui / --port` | off / 8765 | local web UI |
| `--doctor` | — | verify the installation |

</details>

## ⚙️ Configuration

Precedence: **CLI flags > environment > `lightclip.toml` > defaults.**

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` | your LLM key (set once via `setup.bat` / `--setup`) |
| `OPENAI_BASE_URL` / `ANTHROPIC_BASE_URL` | point at an OpenAI-compatible gateway instead of the official API |
| `OPENAI_MODEL` / `ANTHROPIC_MODEL` | default model id (gateways often serve different names) |

Optional `lightclip.toml`:

```toml
[asr]       model = "small"            # tiny | base | small | medium
[selection] provider = "openai"        # anthropic | openai
            model = "qwen/qwen3.7-flash:free"
[output]    dir = "shorts"
            style = "center"           # center | blur | face
            captions = true
[render]    preset = "veryfast"
            crf = 20
```

Keys are read from the environment only — never stored in project files.

## 🧠 How it works

```
video ──► S1 ffmpeg extracts 16 kHz mono audio
      ──► S2 faster-whisper (CTranslate2, int8, CPU) → words with timestamps
      ──► S3 YOUR LLM reads the transcript → picks N moments (structured JSON)
      ──► deterministic boundary snapping (never mid-word)
      ──► S4 ffmpeg: cut → reframe → loudness-normalize → burn ASS captions
      ──► S5 clips + paste-ready metadata (.txt) + manifest.json
```

Design rules: the API only ever sees transcript *text* (never video); only the
20–60 s clip windows are ever re-encoded; stage outputs are cached per source hash,
so re-runs (more clips, different style) skip transcription and selection entirely.

## 💰 What it costs

| Mode | Key needed | Cost |
|---|---|---|
| `--heuristic` | **no key** | **$0** — offline selection, generic titles |
| Free gateway model (e.g. `qwen/...:free`) | free key | **$0** — daily request caps may apply |
| Paid LLM key (OpenAI / Anthropic / gateway) | yes | ~$0.01–0.30 per hour of video |

Transcription, cutting, reframing and captions always run locally and free.
The speech model downloads once (~75 MB `tiny` … ~460 MB `small`).

## 📊 Benchmarks

Measured on an 8-core laptop-class CPU, 1-hour 1080p input, `--heuristic`, 8 clips:

| NFR | Result |
|---|---|
| Peak RAM (whole process tree) | **0.62 GB** (budget: ≤ 2.5 GB) ✅ |
| Wall time — `tiny` ASR | ~5 min ✅ |
| Wall time — `small` ASR (default) | ~55 min ⚠️ laptop CPUs: use `base`/`tiny` for speed |

Full per-stage breakdown: [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) · reproducible via
`python tests/benchmark.py`.

## 🗺️ Roadmap

- [x] Core pipeline (transcribe → select → cut → caption)
- [x] URL input, batch mode, offline heuristic, face-tracked crop, fast drafts
- [x] Local web UI with drag & drop
- [ ] Keyword-highlight captions & zoom punch-ins
- [ ] Speaker diarization for two-person podcasts
- [ ] Publish integrations (YouTube/TikTok upload, user OAuth)
- [ ] macOS/Linux setup scripts to match `setup.bat`

## ❓ FAQ

<details>
<summary><strong>Is my video uploaded anywhere?</strong></summary>
No. Media never leaves your machine. Only the transcript <em>text</em> is sent to the
LLM provider you configured.
</details>

<details>
<summary><strong>Can I use it without any API key?</strong></summary>
Yes — <code>--heuristic</code> (or the "Free offline mode" checkbox in the UI) selects
moments with local heuristics. Zero cost, zero network, generic clip titles.
</details>

<details>
<summary><strong>Why is my run slow?</strong></summary>
Transcription dominates. On laptop-class CPUs use <code>--asr-model base</code> or
<code>tiny</code> (see <a href="docs/BENCHMARKS.md">benchmarks</a>). Renders themselves
run ~10× realtime.
</details>

<details>
<summary><strong>Does it work on movies with music?</strong></summary>
Clipping works; caption accuracy drops on music-heavy or noisy audio (that's a
Whisper reality, not a LightClip one). VAD filtering is on by default.
</details>

<details>
<summary><strong>Commercial use?</strong></summary>
The software costs nothing and has no usage caps — what you can legally publish
depends on the rights to the source footage, as with any editor.
</details>

## 📝 Changelog

Full engineering history (approach, decisions, measured outcomes) lives in
[`docs/WORKLOG.md`](docs/WORKLOG.md). Highlights:

- **v2.0.1** — repo restructure (`build/` + `docs/`), `.gitignore`, OSS README, housekeeping
- **v2.0.0** — local web UI (drag & drop, live progress, results gallery)
- **v1.0.1** — `--setup` wizard, `setup.bat`, gateway model overrides
- **v1.0.0** — batch mode, `--doctor`, measured NFR benchmarks
- **v0.2.0** — face-tracked crop, fast drafts, caption styling
- **v0.1.0** — core pipeline + URL input + offline heuristic

## 🤝 Contributing

Issues and PRs welcome. Keep PRs small; run `python -m pytest tests/ -q` before
submitting. Deeper engineering context lives in [`docs/`](docs/) — PRD, technical
design, benchmarks, and the worklog.

## 📄 License

To be finalized before the first public release — **MIT** is the recommended default
(all dependencies are MIT/Apache-compatible).

## 🙏 Acknowledgments

- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) & [CTranslate2](https://github.com/OpenNMT/CTranslate2) — CPU-efficient ASR
- [ffmpeg](https://ffmpeg.org/) — everything media
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — URL fetching
- [OpenCV](https://github.com/opencv/opencv) + [opencv_zoo YuNet](https://github.com/opencv/opencv_zoo) — face detection
- [pydantic](https://docs.pydantic.dev/) — schema validation of LLM output
- [OpusClip](https://www.opus.pro/), [SupaClip](https://www.supoclip.com/), [ClipKitty](https://getclipkitty.com/) — the products that proved the category (and priced out the creators this tool is for)
