"""Full-pipeline smoke test for LightClip — no API key needed.

Generates a ~80 s test video (Windows TTS speech over a test pattern), then runs
probe -> extract -> transcribe (tiny) -> FakeProvider selection -> snap -> render
and asserts the outputs (codec, dimensions, decode, caption text sync).

Leaves caches in work_smoke/ so the CLI path can be exercised afterwards:

    python tests/smoke_e2e.py
    python lightclip.py <video> --clips 2 --min 5 --max 15 --asr-model tiny \
        --workdir work_smoke --outdir shorts_cli
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parent  # outputs live at the project root
sys.path.insert(0, str(ROOT))

from lightclip import asr, captions, media, models, selector, util  # noqa: E402

TEST_DIR = Path(tempfile.gettempdir()) / "lightclip_smoke"
VIDEO = TEST_DIR / "test_video.mp4"
WORK = PROJECT / "work_smoke"
OUTDIR = PROJECT / "shorts_smoke"

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


def make_test_video() -> Path:
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    if VIDEO.exists():
        return VIDEO
    wav = TEST_DIR / "speech.wav"
    txt = TEST_DIR / "speech.txt"
    txt.write_text(SPEECH, encoding="utf-8")
    print("[smoke] synthesizing speech via Windows SAPI TTS…")
    ps = (
        "Add-Type -AssemblyName System.Speech;"
        f"$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        f"$s.SetOutputToWaveFile('{wav}');"
        f"$s.Speak((Get-Content -Raw '{txt}'));"
        "$s.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True,
                   capture_output=True, text=True)
    if not wav.exists():
        raise RuntimeError("TTS failed to produce speech.wav")
    print("[smoke] muxing speech over a 1080p test pattern…")
    subprocess.run(
        ["ffmpeg", "-y", "-nostdin",
         "-f", "lavfi", "-i", "testsrc2=duration=90:size=1920x1080:rate=30",
         "-i", str(wav),
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "96k", "-shortest", str(VIDEO)],
        check=True, capture_output=True, text=True)
    return VIDEO


class FakeProvider(selector.BaseProvider):
    """Picks deterministic windows from the transcript text — no network, no key."""

    name = "fake"
    model = "fake-1"

    def select(self, system: str, user: str) -> str:
        spans = [(float(a), float(b))
                 for a, b in re.findall(r"\[([\d.]+) - ([\d.]+)\]", user)]
        clips, i = [], 0
        while i < len(spans) and len(clips) < 2:
            start = spans[i][0]
            j = i
            while j < len(spans) - 1 and spans[j][1] - start < 11.0:
                j += 1
            clips.append({
                "start_sec": start, "end_sec": spans[j][1],
                "title": f"Smoke clip {len(clips) + 1}",
                "hook": "A hook line for the smoke test.",
                "caption": "A caption for the smoke test.",
                "hashtags": ["#test"], "virality_score": 80,
                "reason": "smoke test window"})
            i = j + 1
        return json.dumps({"clips": clips})


def window_cards(t: models.Transcript, start: float, end: float, fps: float) -> list[dict]:
    words = [w for seg in t.segments for w in seg.words
             if w.end > start + 1e-3 and w.start < end - 1e-3]
    if not words:
        return []
    sub = models.Transcript(segments=[models.Segment(
        start=0.0, end=end - start, text="",
        words=[models.Word(start=max(0.0, w.start - start),
                           end=max(0.01, w.end - start), word=w.word) for w in words])])
    return captions.group_words(sub, fps=fps)


def main() -> int:
    t0 = time.perf_counter()
    video = make_test_video()
    print(f"[smoke] test video: {video}")

    pr = media.probe(str(video))
    assert pr.has_audio and pr.height == 1080, f"probe unexpected: {pr}"

    src_hash = util.sha256_file(video)
    cdir = util.cache_dir(WORK, src_hash)
    wav = cdir / "audio.wav"
    media.extract_audio(str(video), str(wav))

    tp = cdir / "transcript.json"
    if tp.exists():
        t = models.Transcript.model_validate_json(tp.read_text(encoding="utf-8"))
    else:
        print("[smoke] transcribing (tiny, int8 — downloads model on first run)…")
        t = asr.transcribe(str(wav), model_size="tiny")
        t.source_hash = src_hash
        tp.write_text(t.model_dump_json(indent=2), encoding="utf-8")
    assert t.segments, "no speech transcribed"
    n_words = sum(len(s.words) for s in t.segments)
    print(f"[smoke] transcript: language={t.language}, {n_words} words")

    result = selector.select_clips(t, FakeProvider(), 2, 5.0, 15.0)
    clips = selector.snap_clips(result, t, 2, 5.0, 15.0)
    assert len(clips) == 2, f"expected 2 snapped clips, got {len(clips)}"
    util.write_json(cdir / "clips.json", [c.model_dump() for c in clips])

    OUTDIR.mkdir(exist_ok=True)
    for c in clips:
        cards = window_cards(t, c.start, c.end, pr.fps or 30.0)
        assert cards, f"no caption cards for clip {c.id}"
        ass = cdir / f"captions_{c.id:02d}.ass"
        captions.render_ass(cards, str(ass), out_w=1080, out_h=1920)
        out_file = OUTDIR / f"clip_{c.id:02d}__{util.slugify(c.title)}.mp4"
        cmd = media.build_render_cmd(str(video), c.start, c.duration, str(out_file),
                                     ass_file=str(ass))
        media.render_clip(cmd, cwd=str(cdir))

        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,codec_name", "-of", "json", str(out_file)],
            capture_output=True, text=True)
        info = json.loads(r.stdout)["streams"][0]
        assert (info["width"], info["height"]) == (1080, 1920), info
        assert info["codec_name"] == "h264", info

        d = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out_file), "-f", "null", "-"],
                           capture_output=True, text=True)
        assert d.returncode == 0 and not d.stderr.strip(), f"decode issue: {d.stderr[:300]}"

        # caption text must come from the spoken words in the clip window (A2 analog)
        win_text = " ".join(
            w.word for seg in t.segments for w in seg.words
            if c.start - 0.3 <= w.start <= c.end).upper()
        for card in cards:
            for word in card["text"].split():
                assert word in win_text, f"caption word '{word}' not in window transcript"

        print(f"[smoke] clip {c.id}: {c.start:.1f}-{c.end:.1f} s "
              f"({c.duration:.1f} s, {len(cards)} cards) -> {out_file.name} OK")

    (OUTDIR / "manifest.json").write_text(
        json.dumps([c.model_dump() for c in clips], indent=2, ensure_ascii=False),
        encoding="utf-8")
    print(f"[smoke] PASS in {time.perf_counter() - t0:.0f} s — outputs in {OUTDIR}/, caches in {WORK}/")
    print(f"[smoke] now exercise the CLI: python lightclip.py \"{video}\" --clips 2 "
          f"--min 5 --max 15 --asr-model tiny --workdir {WORK.name} --outdir shorts_cli")
    return 0


if __name__ == "__main__":
    sys.exit(main())
