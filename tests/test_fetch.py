"""Offline tests for fetch.py (no network)."""
import sys

import pytest

from lightclip import fetch


def test_is_url():
    assert fetch.is_url("https://youtu.be/x")
    assert fetch.is_url("http://example.com/v.mp4")
    assert fetch.is_url("WWW.EXAMPLE.COM/v")
    assert not fetch.is_url(r"C:\Videos\movie.mp4")
    assert not fetch.is_url("episode-42.mp4")
    assert not fetch.is_url("")


def test_cached_download_short_circuits(tmp_path, capsys):
    fake = tmp_path / "source.mp4"
    fake.write_bytes(b"0" * 1_100_000)
    out = fetch.download("https://example.com/v.mp4", tmp_path)
    assert out == fake.resolve()
    assert "cached download" in capsys.readouterr().out


def test_missing_ytdlp_clear_error(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "yt_dlp", None)  # import raises ImportError
    with pytest.raises(fetch.FetchError, match="pip install yt-dlp"):
        fetch.download("https://example.com/v.mp4", tmp_path)
