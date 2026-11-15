"""Face-tracked crop (FR-21 'face' style) via OpenCV YuNet.

v0.2 implementation choice per tech doc §S4: STATIC smart-crop — sample frames
across the clip window (~4 fps), take the dominant (largest) face's center x in
each sampled frame, and crop a fixed out_w-wide column centered on the median.
Jitter-free by design; a dynamic per-frame crop stays a future option.

Falls back to None (caller uses center crop) when face coverage is below
threshold or the cover-scaled frame leaves no horizontal slack (portrait
sources).

Optional dependency: opencv-python (lazy import). The YuNet model is vendored
at lightclip/models/face_detection_yunet_2023mar.onnx (MIT, opencv_zoo).
"""
from __future__ import annotations

import statistics
from pathlib import Path

MODEL_NAME = "face_detection_yunet_2023mar.onnx"
MODEL_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/"
    "models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
)


class FaceTrackError(Exception):
    """Raised when face tracking cannot run (missing opencv-python or model)."""


def model_path() -> Path:
    p = Path(__file__).resolve().parent / "models" / MODEL_NAME
    if not p.exists():
        raise FaceTrackError(
            f"YuNet model not found at {p}\n"
            f'  Download it once:  curl -L -o "{p}" "{MODEL_URL}"'
        )
    return p


def sample_face_centers(
    video: str,
    start: float,
    dur: float,
    samples: int = 40,
    score_threshold: float = 0.6,
) -> tuple[list[float], float]:
    """Sample frames across the window; return (largest-face center x per detected
    frame in SOURCE pixels, coverage = detected/sampled)."""
    try:
        import cv2
    except ImportError as e:
        raise FaceTrackError(
            "opencv-python is required for --style face.\n"
            "  Install it once:  pip install opencv-python"
        ) from e

    mp = model_path()
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise FaceTrackError(f"could not open video for face sampling: {video}")
    try:
        detector = cv2.FaceDetectorYN.create(
            str(mp), "", (320, 320), score_threshold=score_threshold
        )
        centers: list[float] = []
        detected = 0
        dur = max(dur, 0.1)
        n = max(1, samples)
        for i in range(n):
            t = start + dur * (i + 0.5) / n  # mid-bucket sampling
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            h, w = frame.shape[:2]
            detector.setInputSize((w, h))
            _, faces = detector.detect(frame)
            if faces is None or len(faces) == 0:
                continue
            detected += 1
            face = max(faces, key=lambda f: float(f[2]) * float(f[3]))  # dominant = largest
            centers.append(float(face[0]) + float(face[2]) / 2.0)
        return centers, detected / n
    finally:
        cap.release()


def smart_crop_x(
    video: str,
    start: float,
    dur: float,
    src_w: int,
    src_h: int,
    out_w: int,
    out_h: int,
    samples: int = 40,
    min_coverage: float = 0.6,
) -> int | None:
    """Crop x (in the cover-scaled render frame) for face style, or None to fall
    back to center crop.

    Scale factor s = max(out_w/src_w, out_h/src_h) — the same cover-scale the
    render applies before cropping; the column centers on the median dominant-
    face x and is clamped to stay inside the frame.
    """
    centers, coverage = sample_face_centers(video, start, dur, samples=samples)
    if not centers or coverage < min_coverage:
        return None
    s = max(out_w / max(1, src_w), out_h / max(1, src_h))
    slack = src_w * s - out_w
    if slack < 40:  # no meaningful horizontal freedom (portrait/narrow source)
        return None
    x0 = round(statistics.median(centers) * s - out_w / 2)
    return max(0, min(int(x0), int(slack)))
