"""LightClip data models (pydantic v2).

Single source of truth for every schema exchanged between stages:
- Transcript / Segment / Word        (S2 transcription output, cached)
- ClipCandidate / SelectionResult    (S3 raw LLM output schema)
- ClipOut                            (S3 after deterministic snapping)
- Manifest                           (S5 run summary)
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class Word(BaseModel):
    start: float
    end: float
    word: str


class Segment(BaseModel):
    start: float
    end: float
    text: str
    words: list[Word] = Field(default_factory=list)


class Transcript(BaseModel):
    source_hash: str = ""
    language: str = "unknown"
    duration: float = 0.0
    segments: list[Segment] = Field(default_factory=list)


class ClipCandidate(BaseModel):
    """Schema the LLM must return per clip (before deterministic snapping)."""

    start_sec: float = Field(..., description="Start time; copied exactly from the transcript")
    end_sec: float = Field(..., description="End time; copied exactly from the transcript")
    title: str
    hook: str = ""
    caption: str = ""
    hashtags: list[str] = Field(default_factory=list)
    virality_score: int = Field(50, ge=0, le=100)
    reason: str = ""


class SelectionResult(BaseModel):
    clips: list[ClipCandidate]


class ClipOut(BaseModel):
    """A snapped, render-ready clip. snap_adjustments records every fix applied."""

    id: int
    start: float
    end: float
    duration: float
    title: str
    hook: str = ""
    caption: str = ""
    hashtags: list[str] = Field(default_factory=list)
    virality_score: int = 50
    reason: str = ""
    snap_adjustments: list[str] = Field(default_factory=list)


class Manifest(BaseModel):
    source: str
    generated_at: str
    settings: dict = Field(default_factory=dict)
    clips: list[ClipOut] = Field(default_factory=list)
