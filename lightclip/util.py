"""Shared helpers: hashing, cache layout, slugs, time formatting, logging,
exit codes, progress display."""
from __future__ import annotations

import hashlib
import re
import sys
import time
from enum import IntEnum
from pathlib import Path


class ExitCode(IntEnum):
    OK = 0
    GENERIC = 1
    NO_AUDIO = 2
    NO_KEY = 3
    LLM_INVALID = 4
    NO_SPEECH = 5


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cache_dir(work_root: str | Path, source_hash: str) -> Path:
    """Per-source cache dir: work/<hash16>/ holding audio.wav, transcript.json, clips.json."""
    d = Path(work_root) / source_hash[:16]
    d.mkdir(parents=True, exist_ok=True)
    return d


def slugify(title: str, max_len: int = 40) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", title).strip("-").lower()
    return (s[:max_len].rstrip("-")) or "clip"


def even(n: float) -> int:
    """Nearest even int (ffmpeg requires even dimensions for yuv420p)."""
    n = int(round(n))
    return n if n % 2 == 0 else n - 1


def format_ass_time(sec: float) -> str:
    """H:MM:SS.CS (centiseconds), the ASS timestamp format."""
    sec = max(0.0, sec)
    cs = int(round(sec * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def format_srt_time(sec: float) -> str:
    sec = max(0.0, sec)
    ms = int(round(sec * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def format_clock(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def snap_to_frame(t: float, fps: float) -> float:
    """Round a timestamp to the nearest frame boundary (kills caption pop-in jitter)."""
    if fps <= 0:
        return t
    return round(t * fps) / fps


def fmt_dur(sec: float) -> str:
    return f"{sec / 60:.1f} min" if sec >= 90 else f"{sec:.0f} s"


def setup_logging(verbose: bool = False) -> None:
    import logging

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )


class Stage:
    """Prints [k/N] stage lines for the progress display."""

    def __init__(self, total: int = 4):
        self.total = total

    def line(self, k: int, msg: str) -> None:
        print(f"[{k}/{self.total}] {msg}", flush=True)


def write_json(path: str | Path, data) -> None:
    import json

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: str | Path):
    import json

    return json.loads(Path(path).read_text(encoding="utf-8"))


VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".ts", ".m4v"}


def collect_videos(folder: str | Path) -> list[Path]:
    """All video files DIRECTLY inside `folder`, sorted by lowercase name.

    Batch mode (PRD FR-35) is non-recursive by design: one settings set for the
    folder, one output subfolder per video.
    """
    return sorted(
        (p for p in Path(folder).iterdir()
         if p.is_file() and p.suffix.lower() in VIDEO_EXTS),
        key=lambda p: p.name.lower(),
    )
