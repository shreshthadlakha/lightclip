"""URL input: download a video via yt-dlp (PRD FR-2 / UC-07).

Lazy yt-dlp import — URL input is an optional capability; local-file users never
need it. Downloads go to work/url_<hash>/source.mp4 and are cached: re-runs skip
the fetch entirely, and the normal file-hash transcript cache applies afterwards.
"""
from __future__ import annotations

import time
from pathlib import Path

_MIN_CACHED_BYTES = 1_000_000  # ignore obviously broken cached files


class FetchError(Exception):
    """Raised when a URL cannot be turned into a local video file."""


def is_url(text: str) -> bool:
    t = text.strip().lower()
    return t.startswith(("http://", "https://", "www."))


def _hook(d: dict) -> None:
    if d.get("status") == "downloading":
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        done = d.get("downloaded_bytes") or 0
        if total:
            print(f"\r        downloading… {int(done * 100 / total):3d}%  "
                  f"({done / 1e6:.0f} / {total / 1e6:.0f} MB)",
                  end="", flush=True)
    elif d.get("status") == "finished":
        print("\r        download complete, merging…            ", flush=True)


def download(url: str, dest_dir: str | Path, max_height: int = 1080) -> Path:
    """Download `url` into dest_dir/source.mp4 (cached skip). Returns resolved path."""
    dest = Path(dest_dir)
    expected = dest / "source.mp4"
    if expected.exists() and expected.stat().st_size > _MIN_CACHED_BYTES:
        print(f"        cached download found ({expected.stat().st_size / 1e6:.0f} MB), skipping fetch")
        return expected.resolve()

    try:
        import yt_dlp
    except ImportError as e:
        raise FetchError(
            "yt-dlp is required for URL input.\n"
            "  Install it once:  pip install yt-dlp"
        ) from e

    dest.mkdir(parents=True, exist_ok=True)
    opts = {
        "format": (
            f"bv*[height<={max_height}][ext=mp4]+ba[ext=m4a]/"
            f"b[height<={max_height}][ext=mp4]/bv*[height<={max_height}]+ba/b"
        ),
        "outtmpl": str(dest / "source.%(ext)s"),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [_hook],
        "retries": 3,
    }
    t0 = time.perf_counter()
    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            ydl.download([url.strip()])
        except yt_dlp.utils.DownloadError as e:
            raise FetchError(f"download failed: {e}") from e

    if not expected.exists() or expected.stat().st_size == 0:
        found = [c for c in dest.glob("source.*") if c.stat().st_size > 0]
        if not found:
            raise FetchError(f"download produced no video file in {dest}")
        return found[0].resolve()
    print(f"        done ({time.perf_counter() - t0:.0f} s, {expected.stat().st_size / 1e6:.0f} MB)")
    return expected.resolve()
