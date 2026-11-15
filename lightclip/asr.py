"""S2 — Transcription: faster-whisper wrapper + transcript JSON cache I/O.

The heavier import (`faster_whisper`) is deliberately lazy, inside transcribe():
importing this module must stay cheap and side-effect free.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import models


def transcribe(
    wav_path: str,
    model_size: str = "small",
    language: str | None = None,
    vad: bool = True,
    cpu_threads: int | None = None,
) -> models.Transcript:
    """Transcribe a wav file into a Transcript with word-level timestamps.

    source_hash is left empty — the caller fills it (it owns the cache key).
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "faster-whisper is not installed. Install it with: pip install faster-whisper"
        ) from exc

    model = WhisperModel(
        model_size,
        device="cpu",
        compute_type="int8",
        cpu_threads=cpu_threads or (os.cpu_count() or 4),
    )
    segments, info = model.transcribe(
        wav_path,
        vad_filter=vad,
        word_timestamps=True,
        language=language,
    )
    out: list[models.Segment] = []
    for seg in segments:  # consume the generator fully — this drives the transcription
        words = [
            models.Word(start=w.start, end=w.end, word=w.word)
            for w in (seg.words or [])
        ]
        out.append(models.Segment(start=seg.start, end=seg.end, text=seg.text, words=words))
    return models.Transcript(
        source_hash="",
        language=info.language,
        duration=info.duration,
        segments=out,
    )


def save_transcript(t: models.Transcript, path: str | Path) -> None:
    """Persist a Transcript as JSON (pydantic dump, UTF-8)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(t.model_dump_json(indent=2), encoding="utf-8")


def load_transcript(path: str | Path) -> models.Transcript:
    """Load a Transcript previously written by save_transcript."""
    return models.Transcript.model_validate_json(Path(path).read_text(encoding="utf-8"))
