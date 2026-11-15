"""S3 — LLM highlight selection: provider abstraction, prompts, schema validation,
deterministic boundary snapping.

No network access happens at import time; providers import their SDK lazily and
fail fast on missing keys, so the module itself is safe to import offline.
"""
from __future__ import annotations

import bisect
import json
import logging
import os
import re

from pydantic import ValidationError

from . import models

logger = logging.getLogger(__name__)

CHUNK_MAX_CHARS = 80_000  # ≈20k tokens at ~4 chars/token
_SNAP_TOLERANCE = 0.5  # s; LLM timestamp drift we accept before giving up
_PRE_ROLL = 0.3  # s; start clips slightly before the sentence boundary


class MissingKeyError(Exception):
    """Raised when the provider's API key env var is not set."""


class LLMSelectionError(Exception):
    """Raised when the LLM fails to return schema-valid JSON after all retries."""


# ---------------------------------------------------------------- providers


class BaseProvider:
    name: str = "base"
    model: str = ""

    def select(self, system: str, user: str) -> str:
        """Send one prompt pair, return the raw completion text."""
        raise NotImplementedError


class AnthropicProvider(BaseProvider):
    name = "anthropic"

    def __init__(self) -> None:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise MissingKeyError(
                "ANTHROPIC_API_KEY is not set. Set it in your environment "
                "(e.g. `setx ANTHROPIC_API_KEY sk-ant-...` then reopen the terminal), "
                "or pass --provider openai with OPENAI_API_KEY."
            )
        # ANTHROPIC_MODEL lets gateways serve different ids; default per tech doc.
        self.model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
        self._api_key = key
        self._client = None  # anthropic SDK is imported lazily on first use

    def select(self, system: str, user: str) -> str:
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(api_key=self._api_key)
        resp = self._client.messages.create(
            model=self.model,
            system=system,
            messages=[{"role": "user", "content": user}],
            temperature=0.3,
            max_tokens=4000,
        )
        return resp.content[0].text


class OpenAIProvider(BaseProvider):
    name = "openai"

    def __init__(self) -> None:
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise MissingKeyError(
                "OPENAI_API_KEY is not set. Set it in your environment "
                "(e.g. `setx OPENAI_API_KEY sk-...` then reopen the terminal), "
                "or pass --provider anthropic with ANTHROPIC_API_KEY."
            )
        # OPENAI_MODEL lets OpenAI-compatible gateways serve different ids
        # (e.g. qwen/qwen3.7-flash:free); default per tech doc.
        self.model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        self._api_key = key
        self._client = None  # openai SDK is imported lazily on first use

    def select(self, system: str, user: str) -> str:
        if self._client is None:
            import openai

            self._client = openai.OpenAI(api_key=self._api_key)
        resp = self._client.chat.completions.create(
            model=self.model,
            temperature=0.3,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content


# ---------------------------------------------------------------- prompting


def transcript_to_text(t: models.Transcript) -> str:
    """One line per segment; these timestamps are what the LLM must copy exactly."""
    return "\n".join(f"[{s.start:.2f} - {s.end:.2f}] {s.text}" for s in t.segments)


def chunk_text(text: str, max_chars: int = CHUNK_MAX_CHARS) -> list[str]:
    """Split on line boundaries keeping chunks <= max_chars (~20k tokens)."""
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    cur = ""
    for line in text.splitlines():
        candidate = line if not cur else f"{cur}\n{line}"
        if len(candidate) <= max_chars:
            cur = candidate
            continue
        if cur:
            chunks.append(cur)
        if len(line) > max_chars:  # degenerate: one segment line longer than the cap
            chunks.extend(line[i : i + max_chars] for i in range(0, len(line), max_chars))
            cur = ""
        else:
            cur = line
    if cur:
        chunks.append(cur)
    return chunks


_CLIP_SCHEMA_HINT = (
    '{"clips": [{"start_sec": <float copied from the transcript>, '
    '"end_sec": <float copied from the transcript>, "title": "...", "hook": "...", '
    '"caption": "...", "hashtags": ["#..."], "virality_score": <0-100>, '
    '"reason": "..."}]}'
)


def build_prompts(
    chunk: str, n: int, min_s: float, max_s: float, map_stage: bool
) -> tuple[str, str]:
    """Build (system, user) prompts for one LLM call.

    - map_stage=True: chunk is a portion of the transcript; ask for the top 3
      candidate moments from it.
    - map_stage=False: single-pass selection when chunk is the transcript text;
      reduce stage when chunk is the merged candidates JSON produced by the map
      pass (starts with '{"clips"') — pick exactly n winners from it.
    """
    count = 3 if map_stage else n
    ask = (
        f"Identify the top {count} candidate moments from this portion of the transcript."
        if map_stage
        else f"Select exactly {n} winning clips."
    )
    system = (
        "You are a short-form video editor. From source material with timestamps, "
        f"pick the {count} most engaging self-contained moments for TikTok/Shorts.\n"
        "Rules:\n"
        f"- Each clip: {min_s:g}-{max_s:g} seconds.\n"
        "- Clip must start at a sentence beginning and end at a sentence end.\n"
        "- Prefer: strong hooks, contrarian takes, stories with payoffs, concrete "
        "numbers, emotional moments.\n"
        "- Avoid: mid-topic segments, jokes needing setup, filler.\n"
        "- Timestamps MUST be copied exactly from the transcript — never invent times.\n"
        "- Return ONLY valid JSON matching the requested schema, no commentary."
    )
    if map_stage:
        user = (
            f"Below is a portion of a longer transcript with timestamps:\n\n"
            f"{chunk}\n\n{ask}\n"
            f"Return ONLY valid JSON in exactly this shape:\n{_CLIP_SCHEMA_HINT}"
        )
    elif chunk.lstrip().startswith('{"clips"'):
        user = (
            "A first pass extracted candidate moments from a long transcript. "
            "Below are the merged candidates as JSON:\n\n"
            f"{chunk}\n\n{ask}\n"
            "Pick the winners and write their metadata. Copy their start_sec/end_sec "
            "values exactly — never invent times.\n"
            f"Return ONLY valid JSON in exactly this shape:\n{_CLIP_SCHEMA_HINT}"
        )
    else:
        user = (
            "Below is the transcript with timestamps:\n\n"
            f"{chunk}\n\n{ask}\n"
            f"Return ONLY valid JSON in exactly this shape:\n{_CLIP_SCHEMA_HINT}"
        )
    return system, user


# ---------------------------------------------------------------- validation


def _strip_code_fence(text: str) -> str:
    s = text.strip()
    if not s.startswith("```"):
        return s
    nl = s.find("\n")
    if nl == -1:  # single line: ```json {...} ```
        s = re.sub(r"^```[A-Za-z0-9_-]*", "", s)
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
        return s.strip()
    body = s[nl + 1 :]
    if body.rstrip().endswith("```"):
        body = body.rstrip()[:-3]
    return body.strip()


def parse_selection(raw: str) -> models.SelectionResult:
    """Parse raw LLM output (optionally wrapped in ``` fences) into a SelectionResult."""
    data = json.loads(_strip_code_fence(raw))
    return models.SelectionResult.model_validate(data)


def _call_with_retry(provider: BaseProvider, system: str, user: str) -> models.SelectionResult:
    """Up to 3 attempts; re-send schema/JSON errors for self-correction.

    HTTP 4xx (except 429) fails fast with the provider's message — retrying
    auth/payment errors is pointless. Network errors, 429 and 5xx are retried.
    """
    last_error = ""
    prompt = user
    for _ in range(3):
        try:
            raw = provider.select(system, prompt)
        except Exception as exc:  # noqa: BLE001 — SDK error types vary by provider
            status = getattr(exc, "status_code", None)
            last_error = (
                f"{type(exc).__name__} (HTTP {status}): {exc}"
                if status is not None else f"{type(exc).__name__}: {exc}"
            )
            if status is not None and 400 <= status < 500 and status != 429:
                raise LLMSelectionError(f"LLM request rejected (HTTP {status}). {exc}") from exc
            continue  # transient (network / 429 / 5xx): retry
        try:
            return parse_selection(raw)
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            prompt = (
                f"{user}\n\nYour previous output was invalid: {last_error}. "
                "Return ONLY corrected JSON matching the schema."
            )
    raise LLMSelectionError(
        f"LLM call failed after 3 attempts; last error: {last_error}"
    )


# ---------------------------------------------------------------- selection


def select_clips(
    t: models.Transcript,
    provider: BaseProvider,
    n: int,
    min_s: float,
    max_s: float,
) -> models.SelectionResult:
    """Ask the provider for clip candidates over the transcript.

    Transcripts within CHUNK_MAX_CHARS get a single call; longer ones go through
    map (top 3 per chunk) → reduce (pick n winners).
    """
    text = transcript_to_text(t)
    if len(text) <= CHUNK_MAX_CHARS:
        system, user = build_prompts(text, n, min_s, max_s, map_stage=False)
        return _call_with_retry(provider, system, user)

    candidates: list[models.ClipCandidate] = []
    for chunk in chunk_text(text):
        system, user = build_prompts(chunk, n, min_s, max_s, map_stage=True)
        candidates.extend(_call_with_retry(provider, system, user).clips)
    merged = json.dumps(
        {"clips": [c.model_dump() for c in candidates]},
        ensure_ascii=False,
    )
    system, user = build_prompts(merged, n, min_s, max_s, map_stage=False)
    return _call_with_retry(provider, system, user)


# ------------------------------------------------- heuristic (offline) fallback
def heuristic_clips(t: models.Transcript, n: int, min_s: float, max_s: float) -> models.SelectionResult:
    """FR-16 offline fallback: rank speech windows by density x completeness.

    Deterministic and API-free: same transcript -> same clips. Candidate windows
    start at every WORD boundary (word-level starts keep yielding clips even when
    ASR emits long segments), extend to the first word end that reaches ``min_s``,
    and are discarded if they would exceed ``max_s``. Score = words-per-second,
    with a bonus when the window starts and ends on segment (sentence) boundaries.
    Titles stay generic ("Clip 3"); virality_score is a 40-100 density-scaled
    value so the table still sorts.
    """
    words = [w for seg in t.segments for w in seg.words if w.end > w.start]
    if not words:
        return models.SelectionResult(clips=[])

    seg_starts = {round(s.start, 2) for s in t.segments}
    seg_ends = {round(s.end, 2) for s in t.segments}

    candidates: list[tuple[float, float, float]] = []  # (start, end, score)
    for k in range(len(words)):
        start = words[k].start
        m = k
        while m < len(words) and words[m].end - start <= max_s:
            span = words[m].end - start
            if span >= min_s:
                density = (m - k + 1) / span
                bonus = (1.25 if round(start, 2) in seg_starts else 1.0) * (
                    1.25 if round(words[m].end, 2) in seg_ends else 1.0)
                candidates.append((start, words[m].end, density * bonus))
                break  # first boundary reaching min_s is the natural window end
            m += 1

    candidates.sort(key=lambda c: -c[2])
    picked: list[tuple[float, float, float]] = []
    for cand in candidates:
        # non-overlap must be checked against ALL picked intervals, not just the
        # last end — picks are added in score order, which is not time order.
        if all(cand[0] >= p[1] - 1e-9 or cand[1] <= p[0] + 1e-9 for p in picked):
            picked.append(cand)
        if len(picked) >= n:
            break

    if not picked:
        return models.SelectionResult(clips=[])

    top = max(p[2] for p in picked)
    clips = []
    for k, (start, end, score) in enumerate(picked, 1):
        clips.append(models.ClipCandidate(
            start_sec=start, end_sec=end,
            title=f"Clip {k}",
            hook="", caption="", hashtags=[],
            virality_score=int(round(40 + 60 * score / top)) if top > 0 else 50,
            reason="heuristic fallback: speech density x rate-variance (no API key used)",
        ))
    return models.SelectionResult(clips=clips)


# ---------------------------------------------------------------- snapping


def _nearest(boundaries: list[float], target: float) -> float | None:
    """Nearest value to target in the sorted boundary list (None if empty)."""
    if not boundaries:
        return None
    i = bisect.bisect_left(boundaries, target)
    options = boundaries[max(0, i - 1) : min(len(boundaries), i + 1)]
    return min(options, key=lambda v: abs(v - target))


def snap_clips(
    result: models.SelectionResult,
    t: models.Transcript,
    n: int,
    min_s: float,
    max_s: float,
) -> list[models.ClipOut]:
    """Deterministic post-processing of LLM candidates into render-ready clips.

    Every fix is recorded in the clip's snap_adjustments; clips that cannot be
    fixed on segment boundaries are dropped (logged to stderr) — the pipeline
    never emits a broken timeline.
    """
    seg_starts = sorted(s.start for s in t.segments)
    seg_ends = sorted(s.end for s in t.segments)
    kept: list[models.ClipOut] = []

    for cand in sorted(result.clips, key=lambda c: c.start_sec):
        if len(kept) >= n:
            break
        adj: list[str] = []

        # (2) start → nearest segment start within tolerance, minus pre-roll
        start = cand.start_sec
        near = _nearest(seg_starts, start)
        if near is not None and abs(near - start) <= _SNAP_TOLERANCE:
            start = max(0.0, near - _PRE_ROLL)
            adj.append(
                f"start snapped to {start:.2f} (segment start {near:.2f} - {_PRE_ROLL:g} s pre-roll)"
            )
        else:
            adj.append("kept raw start (no boundary within 0.5 s)")

        # (3) end → nearest segment end within tolerance
        end = cand.end_sec
        near = _nearest(seg_ends, end)
        if near is not None and abs(near - end) <= _SNAP_TOLERANCE:
            end = near
            adj.append(f"end snapped to {end:.2f} (nearest segment end)")
        else:
            adj.append("kept raw end (no boundary within 0.5 s)")

        # (4) trim over the cap, then extend below the minimum
        if end - start > max_s:
            fitting = [e for e in seg_ends if start < e <= start + max_s]
            if fitting:
                end = max(fitting)
                adj.append(f"end trimmed to {end:.2f} (largest segment end within {max_s:g} s cap)")
            else:
                end = start + max_s
                adj.append(f"end trimmed to {end:.2f} (no segment end within cap; hard cap applied)")
        if end - start < min_s:
            reaching = [e for e in seg_ends if e >= start + min_s]
            if reaching:
                end = min(reaching)
                adj.append(f"end extended to {end:.2f} to reach the {min_s:g} s minimum")
                if end - start > max_s:
                    adj.append("dropped: minimum-duration extension exceeds the maximum cap")
                    logger.warning(
                        "clip '%s' dropped: extending to %s s minimum exceeds the %s s cap",
                        cand.title, min_s, max_s,
                    )
                    continue
            else:
                adj.append(f"dropped: no segment end reaches the {min_s:g} s minimum")
                logger.warning(
                    "clip '%s' dropped: cannot reach %s s minimum on any segment end",
                    cand.title, min_s,
                )
                continue

        # (5) non-overlap in start order — shift start, extend end to rescue; drop only if unfixable
        if kept and start < kept[-1].end:
            new_start = kept[-1].end
            if end - new_start >= min_s:
                adj.append(f"start moved to {new_start:.2f} to avoid overlap with the previous clip")
                start = new_start
            else:
                reaching = [e for e in seg_ends if e >= new_start + min_s]
                if reaching and min(reaching) - new_start <= max_s:
                    end = min(reaching)
                    adj.append(
                        f"start moved to {new_start:.2f} to avoid overlap with the previous clip; "
                        f"end extended to {end:.2f} to reach the {min_s:g} s minimum"
                    )
                    start = new_start
                else:
                    adj.append(
                        f"dropped: overlaps previous clip ending at {kept[-1].end:.2f} "
                        f"and cannot reach the {min_s:g} s minimum"
                    )
                    logger.warning(
                        "clip '%s' dropped: overlaps previous clip ending at %.2f",
                        cand.title, kept[-1].end,
                    )
                    continue

        kept.append(
            models.ClipOut(
                id=0,
                start=start,
                end=end,
                duration=end - start,
                title=cand.title,
                hook=cand.hook,
                caption=cand.caption,
                hashtags=cand.hashtags,
                virality_score=cand.virality_score,
                reason=cand.reason,
                snap_adjustments=adj,
            )
        )

    for i, clip in enumerate(kept, 1):  # (6) ids 1..k
        clip.id = i
    return kept
