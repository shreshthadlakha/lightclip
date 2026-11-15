"""Offline tests for reframe.py (math + fallbacks; cv2 mocked where needed)."""
import sys

import pytest

from lightclip import reframe


def test_model_vendored():
    assert reframe.model_path().exists()


def test_missing_cv2_clear_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "cv2", None)  # import raises ImportError
    with pytest.raises(reframe.FaceTrackError, match="pip install opencv-python"):
        reframe.sample_face_centers("v.mp4", 0.0, 10.0)


def test_smart_crop_scaling_and_clamp(monkeypatch):
    # 1920x1080 -> 9:16 cover scale s=1.7778; center-face median maps near center column
    monkeypatch.setattr(reframe, "sample_face_centers",
                        lambda *a, **k: ([960.0, 950.0, 970.0], 1.0))
    x = reframe.smart_crop_x("v.mp4", 0.0, 10.0, 1920, 1080, 1080, 1920)
    s = 1920 / 1080
    expected = max(0, min(round(960.0 * s - 1080 / 2), int(1920 * s - 1080)))
    assert x == expected


def test_smart_crop_low_coverage_falls_back(monkeypatch):
    monkeypatch.setattr(reframe, "sample_face_centers",
                        lambda *a, **k: ([500.0], 0.3))
    assert reframe.smart_crop_x("v.mp4", 0.0, 10.0, 1920, 1080, 1080, 1920) is None


def test_smart_crop_portrait_no_slack(monkeypatch):
    monkeypatch.setattr(reframe, "sample_face_centers",
                        lambda *a, **k: ([540.0], 1.0))
    assert reframe.smart_crop_x("v.mp4", 0.0, 10.0, 1080, 1920, 1080, 1920) is None


def test_smart_crop_left_clamp(monkeypatch):
    # face near the left edge of a wide source -> clamps to 0, never negative
    monkeypatch.setattr(reframe, "sample_face_centers",
                        lambda *a, **k: ([100.0], 1.0))
    assert reframe.smart_crop_x("v.mp4", 0.0, 10.0, 3840, 2160, 1080, 1920) == 0
