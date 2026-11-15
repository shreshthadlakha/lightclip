"""Offline tests for selector.py: parsing, chunking, snapping."""
import pytest

from lightclip import models, selector


def make_transcript():
    spans = [(10, 14), (15, 19.5), (20, 25), (26, 30), (31, 36), (37, 42)]
    segs = [
        models.Segment(start=a, end=b, text=f"Segment number {i}.",
                       words=[models.Word(start=a, end=b, word=f"seg{i}")])
        for i, (a, b) in enumerate(spans)
    ]
    return models.Transcript(source_hash="h", language="en", duration=42.0, segments=segs)


def test_parse_selection_strips_fences():
    raw = '```json\n{"clips": [{"start_sec": 10.0, "end_sec": 14.0, "title": "T", "virality_score": 90}]}\n```'
    r = selector.parse_selection(raw)
    assert r.clips[0].title == "T" and r.clips[0].virality_score == 90


def test_chunk_text_splits_on_lines():
    text = "\n".join(f"[{i}.00 - {i + 1.00}] line {i}" for i in range(2000))
    chunks = selector.chunk_text(text, max_chars=8000)
    assert len(chunks) > 1
    assert all(len(c) <= 8000 for c in chunks)


def test_transcript_to_text_format():
    t = make_transcript()
    text = selector.transcript_to_text(t)
    assert text.splitlines()[0] == "[10.00 - 14.00] Segment number 0."


def test_snap_fixes_drifted_boundaries():
    t = make_transcript()
    cand = models.SelectionResult(clips=[
        models.ClipCandidate(start_sec=10.3, end_sec=13.8, title="A"),
        models.ClipCandidate(start_sec=25.0, end_sec=31.0, title="B"),
    ])
    out = selector.snap_clips(cand, t, n=2, min_s=3.0, max_s=8.0)
    assert len(out) == 2
    assert out[0].start == pytest.approx(9.7)   # snapped to 10.0 - 0.3 pre-roll
    assert out[0].end == pytest.approx(14.0)
    assert out[0].snap_adjustments
    assert out[1].start == pytest.approx(25.0)  # no boundary within 0.5 s -> kept
    assert out[1].end == pytest.approx(31.0)


def test_snap_extends_too_short_clip():
    t = make_transcript()
    cand = models.SelectionResult(clips=[models.ClipCandidate(start_sec=20.0, end_sec=22.0, title="S")])
    out = selector.snap_clips(cand, t, n=1, min_s=3.0, max_s=8.0)
    assert out[0].duration == pytest.approx(25.0 - 19.7)


def test_snap_enforces_non_overlap():
    t = make_transcript()
    cand = models.SelectionResult(clips=[
        models.ClipCandidate(start_sec=10.0, end_sec=19.0, title="A"),
        models.ClipCandidate(start_sec=15.0, end_sec=22.0, title="B"),  # overlaps A
    ])
    out = selector.snap_clips(cand, t, n=2, min_s=3.0, max_s=10.0)
    assert len(out) == 2
    assert out[1].start >= out[0].end - 1e-6 or out[1].start >= out[0].end


def test_missing_key_raises():
    import os

    saved = {k: os.environ.pop(k, None) for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY")}
    try:
        with pytest.raises(selector.MissingKeyError):
            selector.AnthropicProvider()
        with pytest.raises(selector.MissingKeyError):
            selector.OpenAIProvider()
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def test_heuristic_picks_non_overlapping_in_bounds():
    t = make_transcript()
    r = selector.heuristic_clips(t, n=2, min_s=3.0, max_s=8.0)
    assert len(r.clips) == 2
    out = selector.snap_clips(r, t, 2, 3.0, 8.0)
    assert len(out) == 2
    assert out[1].start >= out[0].end - 1e-6
    assert all(3.0 <= c.duration <= 8.0 for c in out)


def test_heuristic_is_deterministic_and_generic():
    a = selector.heuristic_clips(make_transcript(), 3, 3.0, 8.0)
    b = selector.heuristic_clips(make_transcript(), 3, 3.0, 8.0)
    assert a == b
    assert a.clips[0].title == "Clip 1"
    assert all(0 <= c.virality_score <= 100 for c in a.clips)


def test_heuristic_handles_long_segments():
    # 5 long segments (25 s each, 30 words): segment-start-only candidates would starve;
    # word-level starts must still yield the requested non-overlapping windows.
    segs, t0 = [], 0.0
    for i in range(5):
        step = 25.0 / 30
        segs.append(models.Segment(
            start=t0, end=t0 + 25.0, text=f"seg{i}",
            words=[models.Word(start=t0 + j * step, end=t0 + (j + 1) * step,
                               word=f"w{i}_{j}") for j in range(30)]))
        t0 += 30.0
    t = models.Transcript(segments=segs)
    r = selector.heuristic_clips(t, 4, 20.0, 60.0)
    assert len(r.clips) == 4
    out = selector.snap_clips(r, t, 4, 20.0, 60.0)
    assert len(out) == 4
    assert all(20.0 <= c.duration <= 60.0 for c in out)
    for a, b in zip(out, out[1:]):
        assert b.start >= a.end - 1e-6
