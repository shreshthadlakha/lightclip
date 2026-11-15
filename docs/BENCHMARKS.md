# LightClip Benchmarks — NFR-1 / NFR-2 validation (PRD A1)

- Run: 2026-11-14T17:00:01 — Windows 11, AMD64, 8 logical cores
- Input: 60 min 1920x1080 test fixture (looped TTS speech), 8,035 words transcribed
- Settings: asr=small int8 CPU, heuristic selection, 8 clips 20-60 s, 9:16 center crop, captions on
- Fixture generation: 7.0 min (one-time, NOT counted in NFR timings)

| Stage | Wall time | Peak RSS (process tree) |
|---|---|---|
| S1 probe | 0 s | 0.00 GB |
| S2 extract audio | 12 s | 0.00 GB |
| S3 transcribe (small, int8) | 53.4 min | 0.62 GB |
| S4 select (heuristic) + snap | 0 s | 0.00 GB |
| S5 render 8 clips (quality, captions) | 72 s | 0.00 GB |
| **Total (NFR-2 window)** | **55.0 min** | **0.62 GB (peak)** |

**NFR-1 (peak RAM <= 2.5 GB): PASS** — peak 0.62 GB
**NFR-2 (1 h input -> clips <= 30 min): FAIL** — 55.0 min

*Render speed and transcription scale with content; heuristic selection is deterministic so these numbers are reproducible on the same machine.*

## Findings (2026-11-14, this run)

- **RAM is a non-issue**: 0.62 GB peak for the whole process tree on an 8 GB laptop —
  ~4× under the NFR-1 budget. No PyTorch, int8 quantization, sequential stages: the
  design held exactly as intended.
- **`small` transcription is the bottleneck on U-class laptop CPUs**: ~1.1× realtime
  here (53.4 min for 60 min audio). Measured `tiny` datapoint (movie test): 2 h 08 min
  of audio in 10.6 min ≈ 12× realtime → `tiny` finishes 1 h in ~5 min; `base` lands
  in between (est. ~15–25 min).
- **Guidance**: on 8-core U-class laptops, use `--asr-model base` (or `tiny`) to stay
  inside the 30-min NFR-2 budget; keep `small`/`medium` for caption quality when time
  doesn't matter (or on desktop-class CPUs).
- Everything except transcription is noise: probe 0 s · extract 12 s · select 0 s ·
  render 72 s for 8 quality clips with burned-in captions.
- Fixture generation: 7.0 min one-time (54 speech loops, 4.3 GB), not counted in NFR timings.
