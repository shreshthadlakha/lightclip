"""Offline tests for batch helpers (util.collect_videos)."""
from lightclip import util


def test_collect_videos(tmp_path):
    (tmp_path / "a.mp4").write_bytes(b"x")
    (tmp_path / "B.MKV").write_bytes(b"x")
    (tmp_path / "c.txt").write_bytes(b"x")
    (tmp_path / "d.webm").write_bytes(b"x")
    (tmp_path / "notes.pdf").write_bytes(b"x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "e.mp4").write_bytes(b"x")
    got = util.collect_videos(tmp_path)
    # sorted by lowercase name; non-video files and subfolders excluded
    assert [p.name for p in got] == ["a.mp4", "B.MKV", "d.webm"]


def test_collect_videos_empty(tmp_path):
    assert util.collect_videos(tmp_path) == []
