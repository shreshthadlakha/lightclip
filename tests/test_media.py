"""Offline tests for media.py command construction (the two fixed ffmpeg bugs)."""
from pathlib import Path

import pytest

from lightclip import media


def cmd(ass=None, style="center"):
    return media.build_render_cmd(
        "in.mp4", 12.5, 43.6, "out.mp4", ass_file=ass, style=style)


def test_no_absolute_to_flag():
    c = cmd()
    assert "-to" not in c, "absolute -to with input seeking misreads timestamps"
    assert c[c.index("-t") + 1] == "43.600"


def test_fast_seek_before_input():
    c = cmd()
    assert c[c.index("-ss") + 1] == "12.500"
    assert c.index("-ss") < c.index("-i")


def test_single_complex_filtergraph():
    c = cmd(ass="captions.ass")
    assert c.count("-filter_complex") == 1
    assert "-vf" not in c, "-vf cannot follow a complex filtergraph on the same output"


def test_no_forced_fps():
    c = cmd()
    assert "-r" not in c, "source fps must be preserved"


def test_caption_filter_uses_bare_filename():
    c = cmd(ass=str(Path("work/ab/captions_01.ass")))
    fc = c[c.index("-filter_complex") + 1]
    assert "ass=captions_01.ass" in fc
    assert "work" not in fc.split("ass=")[1].split("[")[0]


def test_audio_chain_has_loudnorm():
    c = cmd()
    fc = c[c.index("-filter_complex") + 1]
    assert "loudnorm" in fc and "aformat=sample_rates=48000" in fc


def test_phone_compatible_codecs():
    c = cmd()
    assert "yuv420p" in c and "+faststart" in c and "libx264" in c and "aac" in c


def test_blur_variant():
    c = cmd(style="blur")
    fc = c[c.index("-filter_complex") + 1]
    assert "boxblur" in fc and "overlay=(W-w)/2:(H-h)/2" in fc


def test_crop_present_for_center():
    fc = cmd()[cmd().index("-filter_complex") + 1]
    assert "crop=1080:1920" in fc and "scale=1080:1920:force_original_aspect_ratio=increase" in fc


def test_render_clip_raises_with_stderr_tail(monkeypatch):
    import subprocess

    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom\ntail-line")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="tail-line"):
        media.render_clip(["ffmpeg"])


def test_draft_cmd_is_stream_copy():
    c = media.build_draft_cmd("in.mp4", 12.5, 43.6, "out.mp4")
    assert c[c.index("-c") + 1] == "copy"
    assert c[c.index("-ss") + 1] == "12.500"
    assert c[c.index("-t") + 1] == "43.600"
    assert c.index("-ss") < c.index("-i")
    assert "+faststart" in c
    assert "-filter_complex" not in c


def test_crop_x_override():
    c = media.build_render_cmd("in.mp4", 0.0, 10.0, "out.mp4", crop_x=500)
    fc = c[c.index("-filter_complex") + 1]
    assert "crop=1080:1920:500" in fc
    c2 = media.build_render_cmd("in.mp4", 0.0, 10.0, "out.mp4")
    fc2 = c2[c2.index("-filter_complex") + 1]
    assert "crop=1080:1920:" not in fc2  # implicit centering without an override
