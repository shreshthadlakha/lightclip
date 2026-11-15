"""LightClip S5 captions: group word timestamps into cards, render ASS/SRT.

Pure stdlib. Input is the shared Transcript schema (models.Transcript); all
times are clip-relative — the caller slices cards per clip window before
rendering, nothing here shifts timestamps.
"""
from __future__ import annotations

from pathlib import Path

from . import util
from .models import Transcript

# A word ending with one of these closes the current card (sentence/clause break).
_PUNCT = frozenset(".!?:,")
# Braces are ASS override-tag delimiters and brackets are ASR noise; both are
# stripped from every word before any text reaches a subtitle file.
_STRIP_TAGS = str.maketrans("", "", "{}[]")

_ASS_STYLE_FORMAT = (
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
    "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
    "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
    "MarginL, MarginR, MarginV, Encoding"
)
_ASS_EVENT_FORMAT = (
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
)


def group_words(
    t: Transcript,
    fps: float = 30.0,
    max_words: int = 4,
    max_dur: float = 1.2,
    gap: float = 0.6,
) -> list[dict]:
    """Pack transcript words, in order, into caption cards.

    A word joins the open card only while the card stays below ``max_words``
    words, the card's total span (word end - card start) stays within ``max_dur``,
    and it starts within ``gap`` seconds of the previous word's end. A word ending in
    ``.!?:,`` closes the card after itself. Card start/end are snapped to the
    frame grid (util.snap_to_frame) so captions never pop in mid-frame.
    """
    cards: list[dict] = []
    cur: list[tuple[str, float, float]] = []  # (clean text, start, end) per word
    prev_end: float | None = None

    def flush() -> None:
        nonlocal cur
        if not cur:
            return
        cards.append(
            {
                "start": util.snap_to_frame(cur[0][1], fps),
                "end": util.snap_to_frame(cur[-1][2], fps),
                "text": " ".join(word for word, _, _ in cur).upper(),
            }
        )
        cur = []

    for seg in t.segments:
        for w in seg.words:
            text = w.word.translate(_STRIP_TAGS)
            if not text:
                continue  # word was braces/brackets only: nothing to show
            if (
                cur
                and len(cur) < max_words
                and w.end - cur[0][1] <= max_dur
                and prev_end is not None
                and w.start - prev_end <= gap
            ):
                cur.append((text, w.start, w.end))
            else:
                flush()
                cur = [(text, w.start, w.end)]
            prev_end = w.end
            if text[-1] in _PUNCT:
                flush()
    flush()
    return cards


def render_ass(
    cards: list[dict],
    out_path: str,
    out_w: int = 1080,
    out_h: int = 1920,
    font: str = "Arial Black",
    font_size: int | None = None,
    pos: float = 0.72,
    color: str = "FFFFFF",
) -> None:
    """Write ``cards`` as an ASS file sized for an out_w x out_h playfield.

    One Dialogue event per card, Pop style, centered (\\an5) at ``pos`` (0-1
    fraction of frame height, default 72%) with the pop-in animation from the
    design spec. FR-23 styling knobs: ``font``, ``font_size`` (px; defaults to
    64 at the 1080-wide reference scale), ``color`` (hex RGB, converted to the
    ASS &H00BBGGRR order). Card times are already clip-relative; they are only
    formatted here.
    """
    fontsize = font_size if font_size is not None else round(64 * out_h / 1920)
    outline = round(3 * out_h / 1920)
    margin_lr = round(40 * out_w / 1080)  # inert under \\an5\\pos, kept sane for other uses
    margin_v = round(100 * out_h / 1920)
    pos_x, pos_y = round(out_w / 2), round(max(0.0, min(1.0, pos)) * out_h)
    rgb = (color or "FFFFFF").lstrip("#").upper()
    if len(rgb) != 6:
        rgb = "FFFFFF"
    ass_color = f"&H00{rgb[4:6]}{rgb[2:4]}{rgb[0:2]}"  # RGB -> ASS BGR order
    pop_tags = (
        "\\an5\\pos(%d,%d)\\fad(60,0)"
        "\\t(0,110,\\fscx115\\fscy115)\\t(110,180,\\fscx100\\fscy100)" % (pos_x, pos_y)
    )

    lines = [
        "[Script Info]",
        "; LightClip S5 caption output",
        "ScriptType: v4.00+",
        f"PlayResX: {out_w}",
        f"PlayResY: {out_h}",
        "ScaledBorderAndShadow: yes",
        "WrapStyle: 2",
        "",
        "[V4+ Styles]",
        _ASS_STYLE_FORMAT,
        (
            f"Style: Pop,{font},{fontsize},{ass_color},&H000000FF,&H00000000,"
            f"&H80000000,-1,0,0,0,100,100,0,0,1,{outline},0,5,"
            f"{margin_lr},{margin_lr},{margin_v},1"
        ),
        "",
        "[Events]",
        _ASS_EVENT_FORMAT,
    ]
    for card in cards:
        # Standard 10-field event line: Layer=0, start, end, Pop style, empty
        # name, zero margins, empty effect, then the tag-prefixed text.
        lines.append(
            "Dialogue: 0,%s,%s,Pop,,0,0,0,,{%s}%s"
            % (
                util.format_ass_time(card["start"]),
                util.format_ass_time(card["end"]),
                pop_tags,
                card["text"],
            )
        )
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_srt(cards: list[dict], out_path: str) -> None:
    """Write ``cards`` as a plain SubRip sidecar (index, timestamp line, text)."""
    blocks = [
        "%d\n%s --> %s\n%s"
        % (
            i,
            util.format_srt_time(card["start"]),
            util.format_srt_time(card["end"]),
            card["text"],
        )
        for i, card in enumerate(cards, 1)
    ]
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text("\n\n".join(blocks) + ("\n" if blocks else ""), encoding="utf-8")
