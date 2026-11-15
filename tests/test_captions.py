"""Offline tests for captions.py: grouping, ASS output, SRT output."""
from lightclip import captions, models


def make_transcript():
    """8 words, 0.2 s each, 0.05 s gaps; 4th and 8th words end with a period."""
    words, t = [], 0.0
    texts = ["alpha", "beta", "gamma", "delta.", "epsilon", "zeta", "eta", "theta."]
    for txt in texts:
        words.append(models.Word(start=t, end=t + 0.2, word=txt))
        t += 0.25
    return models.Transcript(segments=[models.Segment(start=0, end=t, text="", words=words)])


def test_group_respects_max_words():
    cards = captions.group_words(make_transcript(), fps=30)
    assert all(len(c["text"].split()) <= 4 for c in cards)


def test_group_respects_max_duration():
    cards = captions.group_words(make_transcript(), fps=30, max_words=8, max_dur=1.2)
    assert all(c["end"] - c["start"] <= 1.2 + 1 / 30 + 1e-6 for c in cards)


def test_group_closes_at_punctuation():
    cards = captions.group_words(make_transcript(), fps=30)
    assert cards[0]["text"].startswith("ALPHA")
    assert cards[0]["text"].endswith("DELTA.") or cards[0]["text"].endswith("DELTA")


def test_times_snapped_to_frame_grid():
    cards = captions.group_words(make_transcript(), fps=30)
    for c in cards:
        assert abs(c["start"] * 30 - round(c["start"] * 30)) < 1e-6


def test_ass_structure():
    cards = captions.group_words(make_transcript(), fps=30)
    import tempfile, pathlib
    p = pathlib.Path(tempfile.mkdtemp()) / "cap.ass"
    captions.render_ass(cards, str(p), out_w=1080, out_h=1920)
    content = p.read_text(encoding="utf-8")
    assert "PlayResX: 1080" in content and "PlayResY: 1920" in content
    assert content.count("Dialogue:") == len(cards)
    assert "\\fad(60,0)" in content and "\\pos(540,1382)" in content
    assert content.count("Style: Pop,") == 1


def test_ass_custom_styling():
    cards = captions.group_words(make_transcript(), fps=30)
    import tempfile, pathlib
    p = pathlib.Path(tempfile.mkdtemp()) / "cap.ass"
    captions.render_ass(cards, str(p), out_w=1080, out_h=1920,
                        font="Impact", font_size=80, pos=0.5, color="FFFF00")
    content = p.read_text(encoding="utf-8")
    assert "Style: Pop,Impact,80," in content
    assert "&H0000FFFF" in content      # FFFF00 RGB -> BGR order in ASS
    assert "\\pos(540,960)" in content  # 0.5 * 1920


def test_srt_blocks():
    cards = captions.group_words(make_transcript(), fps=30)
    import tempfile, pathlib
    p = pathlib.Path(tempfile.mkdtemp()) / "cap.srt"
    captions.write_srt(cards, str(p))
    content = p.read_text(encoding="utf-8")
    assert content.count("-->") == len(cards)
    assert "-->" in content.splitlines()[1]
