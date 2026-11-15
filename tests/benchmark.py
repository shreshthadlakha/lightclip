"""NFR-1/2 benchmark: a 1-hour 1080p input through every LightClip stage.

Generates (once) a ~60-min 1920x1080 test video — looped TTS speech over a test
pattern — then runs probe -> extract -> transcribe -> heuristic select -> render
INLINE while a sampler thread records the process-tree RSS at ~4 Hz. Writes
BENCHMARKS.md at the project root with per-stage wall time, peak RSS and the
NFR-1 / NFR-2 verdicts (PRD A1).

Usage:
    python tests/benchmark.py [--asr-model small] [--clips 8]

Offline by design: heuristic selection, no API key. First run downloads the
Whisper model (small = ~460 MB) and takes roughly 20-30 minutes end to end;
the 1-hour fixture video is cached in the temp dir and reused.
"""
from __future__ import annotations

import argparse
import platform
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent  # outputs + docs live at the project root
sys.path.insert(0, str(ROOT))

try:
    import psutil
except ImportError:
    print("error: psutil is required for the benchmark:  pip install psutil")
    sys.exit(2)

from lightclip import asr, captions, media, models, selector, util  # noqa: E402
from lightclip.models import Transcript  # noqa: E402

BENCH_DIR = Path(tempfile.gettempdir()) / "lightclip_bench"
VIDEO = BENCH_DIR / "bench_1h.mp4"
OUTDIR = PROJECT / "bench_out"

SPEECH = (
    "Here is the truth about editing shorts. Nobody needs an expensive subscription to cut videos. "
    "A five year old laptop can do the heavy lifting if you pick the right tools. Most creators spend "
    "thirty dollars every month on cloud services. That adds up to three hundred sixty dollars a year. "
    "The free route takes twenty minutes of processing and zero dollars. You keep every file on your own "
    "machine. No watermarks. No upload waiting rooms. No privacy tradeoffs. "
    "The trick is a simple pipeline. First the tool extracts the audio track. Then a quantized speech "
    "model writes down every word with exact timestamps. A language model reads the transcript and picks "
    "the strongest moments. Finally the video gets cut, reframed to vertical, and burned with pop in "
    "captions. The whole thing runs while you drink coffee. One command. Eight clips. Done. "
    "If you can run a browser, you can run this pipeline."
)


def ensure_bench_video() -> Path:
    """Build the 1-hour fixture once (NOT counted in NFR timings)."""
    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    if VIDEO.exists() and VIDEO.stat().st_size > 100_000_000:
        print(f"[bench] fixture cached: {VIDEO} "
              f"({VIDEO.stat().st_size / 1e9:.1f} GB)")
        return VIDEO
    wav = BENCH_DIR / "speech.wav"
    if not wav.exists():
        txt = BENCH_DIR / "speech.txt"
        txt.write_text(SPEECH, encoding="utf-8")
        print("[bench] synthesizing speech (Windows SAPI TTS)…")
        ps = (
            "Add-Type -AssemblyName System.Speech;"
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            f"$s.SetOutputToWaveFile('{wav}');"
            "$s.Speak((Get-Content -Raw '" + str(txt) + "'));$s.Dispose()"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       check=True, capture_output=True, text=True)
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(wav)], capture_output=True, text=True, check=True)
    speech_dur = float(r.stdout.strip())
    loops = max(1, int(-(-3600 // speech_dur)))
    print(f"[bench] encoding 1-hour 1920x1080 fixture ({loops} speech loops) — one-time…")
    subprocess.run(
        ["ffmpeg", "-y", "-nostdin",
         "-f", "lavfi", "-i", "testsrc2=duration=3600:size=1920x1080:rate=30",
         "-stream_loop", str(loops), "-i", str(wav),
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "96k", "-shortest", str(VIDEO)],
        check=True, capture_output=True, text=True)
    print(f"[bench] fixture ready: {VIDEO.stat().st_size / 1e9:.1f} GB")
    return VIDEO


class TreeSampler:
    """Samples this process + all children RSS into a history list."""

    def __init__(self, interval: float = 0.25):
        self.interval = interval
        self.history: list[tuple[float, int]] = []
        self._stop = threading.Event()
        self._t0 = time.perf_counter()
        self._proc = psutil.Process()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _rss(self) -> int:
        try:
            total = self._proc.memory_info().rss
            for c in self._proc.children(recursive=True):
                try:
                    total += c.memory_info().rss
                except psutil.Error:
                    pass
            return total
        except psutil.Error:
            return 0

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.history.append((time.perf_counter() - self._t0, self._rss()))
            time.sleep(self.interval)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def peak_between(self, t0: float, t1: float) -> int:
        vals = [r for (t, r) in self.history if t0 - 0.01 <= t <= t1 + 0.01]
        return max(vals) if vals else 0


def window_cards(t: Transcript, start: float, end: float, fps: float) -> list[dict]:
    words = [w for seg in t.segments for w in seg.words
             if w.end > start + 1e-3 and w.start < end - 1e-3]
    if not words:
        return []
    sub = Transcript(segments=[models.Segment(
        start=0.0, end=end - start, text="",
        words=[models.Word(start=max(0.0, w.start - start),
                           end=max(0.01, w.end - start), word=w.word) for w in words])])
    return captions.group_words(sub, fps=fps)


def main() -> int:
    ap = argparse.ArgumentParser(description="LightClip NFR-1/2 benchmark")
    ap.add_argument("--asr-model", default="small", choices=["tiny", "base", "small", "medium"])
    ap.add_argument("--clips", type=int, default=8)
    ap.add_argument("--min", dest="min_s", type=float, default=20.0)
    ap.add_argument("--max", dest="max_s", type=float, default=60.0)
    bargs = ap.parse_args()

    util.setup_logging(False)
    t_fixture0 = time.perf_counter()
    video = ensure_bench_video()
    fixture_s = time.perf_counter() - t_fixture0

    sampler = TreeSampler()
    sampler.start()
    stages: list[tuple[str, float, int]] = []
    t_total0 = time.perf_counter()

    def stage(name: str, fn):
        t0 = time.perf_counter()
        result = fn()
        dt = time.perf_counter() - t0
        peak = sampler.peak_between(t0, time.perf_counter())
        stages.append((name, dt, peak))
        print(f"[bench] {name}: {util.fmt_dur(dt)}  (peak RSS {peak / 1e9:.2f} GB)")
        return result

    pr = stage("S1 probe", lambda: media.probe(str(video)))

    src_hash = util.sha256_file(video)
    cdir = util.cache_dir(PROJECT / "work_bench", src_hash)
    wav = cdir / "audio.wav"
    stage("S2 extract audio", lambda: media.extract_audio(str(video), str(wav)))

    tp = cdir / "transcript.json"

    def do_transcribe() -> Transcript:
        t = asr.transcribe(str(wav), model_size=bargs.asr_model)
        t.source_hash = src_hash
        tp.write_text(t.model_dump_json(indent=2), encoding="utf-8")
        return t

    if tp.exists():
        t0 = time.perf_counter()
        t = models.Transcript.model_validate_json(tp.read_text(encoding="utf-8"))
        stages.append(("S3 transcribe (cached)", time.perf_counter() - t0,
                       sampler.peak_between(t0, time.perf_counter())))
        print("[bench] S3 transcribe: cached")
    else:
        t = stage(f"S3 transcribe ({bargs.asr_model}, int8)", lambda: do_transcribe())
    n_words = sum(len(s.words) for s in t.segments)
    if not t.segments:
        print("error: fixture produced no speech — benchmark aborted")
        return 2

    def do_select():
        result = selector.heuristic_clips(t, bargs.clips, bargs.min_s, bargs.max_s)
        return selector.snap_clips(result, t, bargs.clips, bargs.min_s, bargs.max_s)

    clips = stage("S4 select (heuristic) + snap", do_select)
    if len(clips) < 2:
        print(f"error: only {len(clips)} clips selected — benchmark aborted")
        return 2

    def do_render() -> None:
        OUTDIR.mkdir(exist_ok=True)
        for c in clips:
            cards = window_cards(t, c.start, c.end, pr.fps or 30.0)
            ass = cdir / f"captions_{c.id:02d}.ass"
            captions.render_ass(cards, str(ass), out_w=1080, out_h=1920)
            out_file = OUTDIR / f"clip_{c.id:02d}__{util.slugify(c.title)}.mp4"
            cmd = media.build_render_cmd(str(video), c.start, c.duration, str(out_file),
                                         ass_file=str(ass))
            media.render_clip(cmd, cwd=str(cdir))
            ass.unlink(missing_ok=True)

    stage(f"S5 render {len(clips)} clips (quality, captions)", do_render)
    if wav.exists():
        wav.unlink(missing_ok=True)  # ~110 MB intermediate — same cleanup policy as the CLI
    total_s = time.perf_counter() - t_total0
    sampler.stop()

    peak_total = max((p for _, _, p in stages), default=0)
    nfr1_pass = peak_total <= 2.5e9
    nfr2_pass = total_s <= 30 * 60

    lines = [
        "# LightClip Benchmarks — NFR-1 / NFR-2 validation (PRD A1)",
        "",
        f"- Run: {datetime.now().isoformat(timespec='seconds')} — "
        f"{platform.system()} {platform.release()}, {platform.machine()}, "
        f"{psutil.cpu_count(logical=True)} logical cores",
        f"- Input: {pr.duration / 60:.0f} min {pr.width}x{pr.height} test fixture "
        f"(looped TTS speech), {n_words:,} words transcribed",
        f"- Settings: asr={bargs.asr_model} int8 CPU, heuristic selection, "
        f"{len(clips)} clips {bargs.min_s:.0f}-{bargs.max_s:.0f} s, 9:16 center crop, captions on",
        f"- Fixture generation: {fixture_s / 60:.1f} min (one-time, NOT counted in NFR timings)",
        "",
        "| Stage | Wall time | Peak RSS (process tree) |",
        "|---|---|---|",
    ]
    for name, dt, peak in stages:
        lines.append(f"| {name} | {util.fmt_dur(dt)} | {peak / 1e9:.2f} GB |")
    lines += [
        f"| **Total (NFR-2 window)** | **{util.fmt_dur(total_s)}** | "
        f"**{peak_total / 1e9:.2f} GB (peak)** |",
        "",
        f"**NFR-1 (peak RAM <= 2.5 GB): {'PASS' if nfr1_pass else 'FAIL'}** — peak {peak_total / 1e9:.2f} GB",
        f"**NFR-2 (1 h input -> clips <= 30 min): {'PASS' if nfr2_pass else 'FAIL'}** — {total_s / 60:.1f} min",
        "",
        "*Render speed and transcription scale with content; heuristic selection is "
        "deterministic so these numbers are reproducible on the same machine.*",
        "",
    ]
    docs_dir = PROJECT / "docs"
    bench_md = docs_dir / "BENCHMARKS.md" if docs_dir.is_dir() else ROOT / "BENCHMARKS.md"
    bench_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[bench] {'PASS' if nfr1_pass and nfr2_pass else 'CHECK'} "
          f"— BENCHMARKS.md written ({total_s / 60:.1f} min total, {peak_total / 1e9:.2f} GB peak)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
