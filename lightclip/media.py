"""ffmpeg/ffprobe wrappers: stream probe, audio extraction, clip render command.

ffmpeg and ffprobe MUST be on PATH: every function here shells out to the bare
binary names via subprocess and never resolves or ships them itself. If they
are missing, calls fail with FileNotFoundError (install: winget Gyan.FFmpeg).
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

_STDERR_TAIL_LINES = 15


class MediaError(Exception):
    """Media-level failure (e.g. the source has no audio track)."""


@dataclass
class ProbeResult:
    width: int
    height: int
    fps: float
    duration: float
    has_audio: bool


def _stderr_tail(stderr: str, lines: int = _STDERR_TAIL_LINES) -> str:
    return "\n".join(stderr.splitlines()[-lines:])


def _ffprobe_json(video: str, select_streams: str, entries: str) -> dict:
    """Run one ffprobe JSON query; RuntimeError with the stderr tail on failure."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", select_streams,
        "-show_entries", entries,
        "-of", "json",
        video,
    ]
    proc = _run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {video}:\n{_stderr_tail(proc.stderr)}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"ffprobe returned non-JSON output for {video}: {e}") from e


def _parse_frame_rate(rate: str) -> float:
    """Parse ffprobe r_frame_rate: '30000/1001' -> 29.97; tolerate '24' and '0/0'."""
    try:
        num, _, den = rate.partition("/")
        den_f = float(den) if den else 1.0
        return float(num) / den_f if den_f else 0.0
    except (TypeError, ValueError):
        return 0.0


def _run(cmd: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    """subprocess.run wrapper: a missing ffmpeg/ffprobe becomes the actionable E-08 error."""
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    except FileNotFoundError as e:
        raise MediaError(
            "ffmpeg/ffprobe not found on PATH.\n"
            "  Install once, then open a NEW terminal:\n"
            "    winget install Gyan.FFmpeg"
        ) from e


def probe(video: str) -> ProbeResult:
    """Probe one video: dimensions, fps, duration, audio presence.

    Two ffprobe JSON calls: the video stream, then an audio-stream presence
    scan. The no-audio MediaError is raised only from that scan's result —
    ffprobe call failures raise RuntimeError instead.
    """
    vinfo = _ffprobe_json(
        video, "v:0", "stream=width,height,r_frame_rate,duration:format=duration"
    )
    streams = vinfo.get("streams") or []
    if not streams:
        raise RuntimeError(f"no video stream found in {video}")
    vs = streams[0]
    # Container fallback (e.g. mkv has no per-stream duration) within the same call.
    raw_dur = vs.get("duration") or (vinfo.get("format") or {}).get("duration")

    ainfo = _ffprobe_json(video, "a", "stream=index")
    if not ainfo.get("streams"):
        raise MediaError(f"{video}: no audio track — LightClip cannot transcribe a video without audio")

    return ProbeResult(
        width=int(vs["width"]),
        height=int(vs["height"]),
        fps=_parse_frame_rate(vs.get("r_frame_rate", "")),
        duration=float(raw_dur) if raw_dur is not None else 0.0,
        has_audio=True,
    )


def extract_audio(video: str, wav_out: str) -> None:
    """Extract 16 kHz mono PCM WAV — the exact input faster-whisper wants."""
    cmd = [
        "ffmpeg", "-y", "-nostdin",
        "-i", video,
        "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le",
        wav_out,
    ]
    proc = _run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(f"audio extraction failed for {video}:\n{_stderr_tail(proc.stderr)}")


def build_render_cmd(
    video: str,
    start: float,
    dur: float,
    out_path: str,
    out_w: int = 1080,
    out_h: int = 1920,
    ass_file: str | None = None,
    style: str = "center",
    preset: str = "veryfast",
    crf: int = 20,
    crop_x: int | None = None,
) -> list[str]:
    """Build the per-clip ffmpeg command: cut + 9:16 reframe + optional ASS burn.

    Deliberate fixes over the tech-doc draft:
    - '-ss' sits BEFORE '-i' (fast seek) and the window length is the OUTPUT
      option '-t'. Never '-to': input seeking resets timestamps, which an
      absolute '-to' would misread.
    - Exactly ONE '-filter_complex' holding everything (video chain + optional
      ass + audio chain); '-vf' is never mixed in.
    - No '-r' anywhere: the source fps is preserved.
    - ass_file enters the filter as its bare basename only: the render runs
      with cwd=<the ass file's folder>, avoiding Windows drive-colon escaping
      inside filter args.
    """
    if style not in ("center", "blur"):
        raise ValueError(f"unknown reframe style: {style!r} (expected 'center' or 'blur')")

    ass_chain = f",ass={Path(ass_file).name}" if ass_file else ""

    if style == "center":
        # crop_x: pre-computed face-track offset in the cover-scaled frame
        # (reframe.smart_crop_x); None = implicit centering. Passed with an
        # explicit y=0 — the offset math assumes full-height columns.
        crop_pos = f":{crop_x}:0" if crop_x is not None else ""
        video_chain = (
            f"[0:v]scale={out_w}:{out_h}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={out_w}:{out_h}{crop_pos}{ass_chain}[v]"
        )
    else:  # blur
        video_chain = (
            f"[0:v]split=2[bg][fg];"
            f"[bg]scale={out_w}:{out_h}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={out_w}:{out_h},boxblur=luma_radius=20:luma_power=2[bgb];"
            f"[fg]scale={out_w}:{out_h}:force_original_aspect_ratio=decrease:flags=lanczos[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2{ass_chain}[v]"
        )

    audio_chain = (
        "[0:a]aformat=sample_rates=48000:channel_layouts=stereo,"
        "loudnorm=I=-16:TP=-1.5:LRA=11[a]"
    )

    return [
        "ffmpeg", "-y", "-nostdin",
        "-ss", f"{start:.3f}",
        "-i", video,
        "-t", f"{dur:.3f}",
        "-filter_complex", f"{video_chain};{audio_chain}",
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        out_path,
    ]


def build_draft_cmd(video: str, start: float, dur: float, out_path: str) -> list[str]:
    """FR-19 fast mode: keyframe-snapped stream copy — near-instant draft cuts.

    '-ss' before '-i' snaps the cut to the nearest PRECEDING keyframe (up to a
    few seconds of drift, by design); '-c copy' never re-encodes, so drafts
    carry no reframe and no caption burn. Draft output only — quality mode is
    the default path.
    """
    return [
        "ffmpeg", "-y", "-nostdin",
        "-ss", f"{start:.3f}",
        "-i", video,
        "-t", f"{dur:.3f}",
        "-map", "0:v:0", "-map", "0:a:0?",
        "-c", "copy",
        "-movflags", "+faststart",
        out_path,
    ]


def render_clip(cmd: list[str], cwd: str | None = None) -> None:
    """Run one render command; RuntimeError carries the last stderr lines on failure."""
    proc = _run(cmd, cwd=cwd)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg render failed (exit {proc.returncode}):\n{_stderr_tail(proc.stderr)}"
        )
