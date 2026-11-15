#!/usr/bin/env python3
"""LightClip — turn long videos into captioned vertical shorts.

    python lightclip.py video.mp4                          # 8 clips, 20-60 s, 9:16, center crop, captions on
    python lightclip.py video.mp4 --clips 12 --min 15 --max 45
    python lightclip.py video.mp4 --style blur --platform tiktok
    python lightclip.py video.mp4 --dry-run                # selection only, prints the clip table
    python lightclip.py "https://youtu.be/..."             # URL input — engine downloads via yt-dlp
    python lightclip.py ./videos-folder                    # batch: every video inside, one settings set
    python lightclip.py --doctor                           # verify the installation and exit

Stages: probe → audio extract → transcribe (faster-whisper) → select (BYOK LLM) → render (ffmpeg).
Media bytes never leave the machine; only the transcript text is sent to the LLM provider.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
import tomllib
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # safe under python -P / vendored interpreters

from lightclip import captions, fetch, media, models, reframe, selector, util
from lightclip.models import Transcript
from lightclip.util import ExitCode, Stage, fmt_dur, format_clock

RATIOS = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}
PLATFORMS = {
    "shorts": {"ratio": "9:16", "max": 60},
    "tiktok": {"ratio": "9:16", "max": 90},
    "reels": {"ratio": "9:16", "max": 90},
}

DEFAULTS: dict = {
    "clips": 8, "min_s": 20.0, "max_s": 60.0,
    "ratio": "9:16", "style": "center", "captions": True,
    "srt": False, "lang": None, "asr_model": "small",
    "provider": None, "llm_model": None, "dry_run": False,
    "outdir": "shorts", "workdir": "work", "preset": "veryfast", "crf": 20,
    "heuristic": False,
    "fast": False, "caption_font": "Arial Black", "caption_size": None,
    "caption_pos": 0.72, "caption_color": "FFFFFF", "caption_words": 4,
}

CONFIG_MAP = {  # lightclip.toml section.key -> settings key
    "asr.model": "asr_model",
    "selection.provider": "provider",
    "selection.model": "llm_model",
    "output.dir": "outdir",
    "output.ratio": "ratio",
    "output.style": "style",
    "output.captions": "captions",
    "render.preset": "preset",
    "render.crf": "crf",
}


def load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception as e:  # noqa: BLE001 — config is optional, never fatal
        print(f"warning: could not parse {path.name}: {e}")
        return {}


def build_settings(args: argparse.Namespace, cfg: dict) -> dict:
    s = dict(DEFAULTS)
    for dotted, target in CONFIG_MAP.items():
        sec, _, key = dotted.partition(".")
        val = cfg.get(sec, {}).get(key)
        if val is not None:
            s[target] = val
    for k, v in vars(args).items():
        if k in s and v is not None:
            s[k] = v
    if args.platform:
        preset = PLATFORMS[args.platform]
        s["ratio"] = preset["ratio"]
        s["max_s"] = preset["max"]
    return s


def resolve_provider(s: dict):
    name = s.get("provider")
    if not name:
        if os.environ.get("ANTHROPIC_API_KEY"):
            name = "anthropic"
        elif os.environ.get("OPENAI_API_KEY"):
            name = "openai"
        else:
            raise selector.MissingKeyError(
                "No LLM API key found.\n"
                "  Set ANTHROPIC_API_KEY or OPENAI_API_KEY in your environment and re-run:\n"
                '    PowerShell:  $env:ANTHROPIC_API_KEY = "sk-ant-..."\n'
                '    or:          $env:OPENAI_API_KEY = "sk-..."'
            )
    provider = selector.AnthropicProvider() if name == "anthropic" else selector.OpenAIProvider()
    if s.get("llm_model"):
        provider.model = s["llm_model"]
    return provider


def window_cards(t: Transcript, start: float, end: float, fps: float,
                 max_words: int = 4) -> list[dict]:
    """Caption cards for one clip window, times shifted to clip-relative."""
    words = [
        w for seg in t.segments for w in seg.words
        if w.end > start + 1e-3 and w.start < end - 1e-3
    ]
    if not words:
        return []
    sub = Transcript(segments=[models.Segment(
        start=0.0, end=end - start, text="",
        words=[models.Word(start=max(0.0, w.start - start),
                           end=max(0.01, w.end - start), word=w.word) for w in words],
    )])
    return captions.group_words(sub, fps=fps, max_words=max_words)


def print_clip_table(clips) -> None:
    for c in clips:
        print(f"  clip {c.id:02d}  {format_clock(c.start)}-{format_clock(c.end)}  "
              f"{c.title[:52]:<52}  score {c.virality_score}")


def _persist_env(name: str, value: str) -> bool:
    """Persist one user-scope environment variable (Windows: setx). The current
    session also sees it immediately; other processes need a NEW terminal."""
    os.environ[name] = value
    if os.name != "nt":
        return False  # non-Windows: caller prints manual export lines
    import subprocess

    r = subprocess.run(["setx", name, value], capture_output=True, text=True)
    return r.returncode == 0


def run_setup(args: argparse.Namespace) -> int:
    """Onboarding: configure the LLM key ONCE, verify, done (FR-31/NFR-9).

    Interactive by default; every prompt can be pre-answered with --setup-*
    flags for scripted installs. Values are persisted as user-scope environment
    variables (Windows: setx) — the same mechanism --doctor inspects. Keys are
    never written into project files (PRD R7).
    """
    print("LightClip setup — your key is used ONLY for highlight selection.")
    print("Transcription, cutting, reframe and captions run locally and free.\n")

    provider = args.setup_provider
    if provider is None:
        try:
            raw = input("Provider — [1] OpenAI-compatible (OpenAI or a gateway)  "
                        "[2] Anthropic  (1/2, Enter = 1): ").strip()
        except EOFError:
            print("\nerror: interactive input unavailable here — "
                  "pass --setup-provider/--setup-key/... flags instead.")
            return ExitCode.GENERIC
        provider = {"": "openai", "1": "openai", "2": "anthropic"}.get(raw.lower())
        if provider is None:
            print("error: please answer 1 or 2.")
            return ExitCode.GENERIC

    def ask(prompt: str, flag_value: str | None) -> str:
        if flag_value is not None:
            return flag_value.strip()
        return input(prompt).strip()

    try:
        key = ask(f"{provider.upper()} API key: ", args.setup_key)
        base = ask("Base URL (Enter = official API; gateways: https://host/v1): ",
                   args.setup_base_url)
        model = ask("Default model id (Enter = provider default): ", args.setup_model)
    except EOFError:
        print("\nerror: interactive input unavailable here — "
              "pass --setup-provider/--setup-key/... flags instead.")
        return ExitCode.GENERIC
    if not key:
        print("error: a key is required — or skip setup and clip free with --heuristic.")
        return ExitCode.GENERIC

    prefix = "ANTHROPIC" if provider == "anthropic" else "OPENAI"
    writes = [(f"{prefix}_API_KEY", key)]
    if base:
        writes.append((f"{prefix}_BASE_URL", base))
    if model:
        writes.append((f"{prefix}_MODEL", model))

    print()
    all_persisted = True
    for name, value in writes:
        ok = _persist_env(name, value)
        all_persisted = all_persisted and ok
        shown = value[:8] + "…" if name.endswith("API_KEY") else value
        print(f"  {'persisted' if ok else 'session-only'}: {name} = {shown}")
    if os.name != "nt" and not all_persisted:
        print("\n  (non-Windows: add these to your shell profile manually)")
        for name, value in writes:
            print(f'    export {name}="{value}"')

    print("\nSetup complete. Clip something:\n")
    print("  python lightclip.py your-video.mp4            # smart titles via your key")
    print("  python lightclip.py your-video.mp4 --heuristic  # zero-key, zero-cost clipping")
    print("  python lightclip.py .\videos-folder            # batch mode\n")
    code = run_doctor()
    if not all_persisted:
        print("\nnote: in THIS window the values are active; new terminals get them "
              "automatically (Windows registry).")
    return code


def run_doctor() -> int:
    """NFR-9 helper: verify the install from zero; nonzero exit on required gaps."""
    import importlib.util
    import shutil

    results: list[tuple[str, bool, bool, str]] = []  # label, ok, required, hint
    py_ok = sys.version_info >= (3, 11)
    results.append((f"python {sys.version.split()[0]} (>= 3.11)", py_ok, True,
                    "install Python 3.11+"))
    for binname in ("ffmpeg", "ffprobe"):
        results.append((f"{binname} on PATH", bool(shutil.which(binname)), True,
                        "winget install Gyan.FFmpeg (open a NEW terminal after)"))
    for mod, hint in (("faster_whisper", "pip install faster-whisper"),
                      ("pydantic", "pip install pydantic")):
        results.append((f"python package: {mod}",
                        importlib.util.find_spec(mod) is not None, True, hint))
    for mod, hint in (("yt_dlp", "pip install yt-dlp — URL input"),
                      ("cv2", "pip install opencv-python — face crop"),
                      ("psutil", "pip install psutil — benchmarks")):
        results.append((f"python package: {mod} (optional)",
                        importlib.util.find_spec(mod) is not None, False, hint))
    model = Path(__file__).resolve().parent / "lightclip" / "models" / reframe.MODEL_NAME
    results.append(("YuNet face model (optional)", model.exists(), False, reframe.MODEL_URL))
    key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY"))
    results.append(("LLM API key in env (optional — else --heuristic)", key, False,
                    '$env:OPENAI_API_KEY = "..."  (+ $env:OPENAI_BASE_URL for gateways)'))
    if os.environ.get("OPENAI_API_KEY"):
        base = os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1 (default)"
        results.append((f"OPENAI_BASE_URL: {base}", True, False,
                        "gateway keys only work with OPENAI_BASE_URL pointed at the gateway"))
        results.append((f"OPENAI_MODEL: {os.environ.get('OPENAI_MODEL') or 'gpt-4o-mini (default)'}",
                        True, False,
                        "set OPENAI_MODEL when your gateway serves different model ids"))

    missing_required = 0
    for label, ok, required, hint in results:
        mark = "OK  " if ok else ("MISS" if required else "opt ")
        line = f"  [{mark}] {label}"
        if not ok:
            line += f"  -> {hint}"
            if required:
                missing_required += 1
        print(line)
    print()
    if missing_required:
        print(f"install incomplete: {missing_required} required item(s) missing (see above).")
        return ExitCode.GENERIC
    print("install looks complete — ready to run.")
    return ExitCode.OK


def run_one(video_arg: str, s: dict, args: argparse.Namespace) -> int:
    """Full pipeline for ONE input (file path or URL). `s` may be a per-video
    settings copy (batch mode); E-04 relaxations stay local to that copy."""
    t_all = time.perf_counter()
    stage = Stage(4)

    video: Path
    source_url: str | None = None
    if fetch.is_url(video_arg):
        source_url = video_arg.strip()
        print("Downloading video (yt-dlp)…", flush=True)
        url_hash = hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:16]
        try:
            video = fetch.download(source_url, Path(s["workdir"]) / f"url_{url_hash}")
        except fetch.FetchError as e:
            print(f"error: {e}")
            return ExitCode.GENERIC
    else:
        video = Path(video_arg).resolve()
        if not video.exists():
            print(f"error: input file not found: {video}")
            return ExitCode.GENERIC

    # ---- S1: probe -----------------------------------------------------------
    stage.line(1, "Probing source")
    try:
        pr = media.probe(str(video))
    except Exception as e:  # noqa: BLE001
        print(f"error: could not probe video: {e}")
        return ExitCode.GENERIC
    print(f"        {pr.width}x{pr.height} @ {pr.fps:.2f} fps, "
          f"{format_clock(pr.duration)}, audio {'OK' if pr.has_audio else 'MISSING'}")
    if not pr.has_audio:
        print("error: this video has no audio track — nothing to transcribe.")
        return ExitCode.NO_AUDIO

    # E-04: relax duration bounds for short sources
    if s["max_s"] > pr.duration / 2:
        s["max_s"] = max(3.0, pr.duration / 2)
        print(f"note: source shorter than 2x max clip length; max relaxed to {s['max_s']:.0f} s")
    if s["min_s"] > pr.duration / 3:
        s["min_s"] = max(2.0, pr.duration / 3)
        print(f"note: min relaxed to {s['min_s']:.0f} s")

    src_hash = util.sha256_file(video)
    cdir = util.cache_dir(s["workdir"], src_hash)

    # ---- S2: audio + transcribe ----------------------------------------------
    stage.line(2, "Extracting audio")
    t0 = time.perf_counter()
    wav = cdir / "audio.wav"
    try:
        media.extract_audio(str(video), str(wav))
    except Exception as e:  # noqa: BLE001
        print(f"error: audio extraction failed: {e}")
        return ExitCode.GENERIC
    print(f"        done ({fmt_dur(time.perf_counter() - t0)})")

    stage.line(3, f"Transcribing (faster-whisper {s['asr_model']}, int8, CPU)")
    t0 = time.perf_counter()
    transcript_path = cdir / "transcript.json"
    if transcript_path.exists():
        t = Transcript.model_validate_json(transcript_path.read_text(encoding="utf-8"))
        print(f"        transcript cached, skipped ({fmt_dur(time.perf_counter() - t0)})")
    else:
        try:
            t = transcribe_stage(wav, s)
        except RuntimeError as e:
            print(f"error: {e}")
            return ExitCode.GENERIC
        t.source_hash = src_hash
        transcript_path.write_text(t.model_dump_json(indent=2), encoding="utf-8")
    n_words = sum(len(seg.words) for seg in t.segments)
    print(f"        language={t.language}, {n_words:,} words ({fmt_dur(time.perf_counter() - t0)})")
    if not t.segments:
        print("error: no speech found in this video (music-only or silent).")
        return ExitCode.NO_SPEECH

    # ---- S3: selection (BYOK LLM) ---------------------------------------------
    stage.line(4, "Selecting highlights" + (" (offline heuristic)" if s["heuristic"] else ""))
    t0 = time.perf_counter()
    clips_path = cdir / "clips.json"
    if clips_path.exists():
        clips = [models.ClipOut.model_validate(c) for c in util.read_json(clips_path)]
        print(f"        selection cached, skipped ({fmt_dur(time.perf_counter() - t0)})")
    elif s["heuristic"]:
        result = selector.heuristic_clips(t, s["clips"], s["min_s"], s["max_s"])
        clips = selector.snap_clips(result, t, s["clips"], s["min_s"], s["max_s"])
        if clips:  # never cache an empty result — the user may retry with different bounds
            util.write_json(clips_path, [c.model_dump() for c in clips])
    else:
        try:
            provider = resolve_provider(s)
            result = selector.select_clips(t, provider, s["clips"], s["min_s"], s["max_s"])
        except selector.MissingKeyError as e:
            print(f"error: {e}\n       tip: rerun with --heuristic to cut clips offline without any API key.")
            return ExitCode.NO_KEY
        except selector.LLMSelectionError as e:
            print(f"error: LLM selection failed after retries: {e}\n"
                  "       tip: rerun with --heuristic to cut clips offline without any API key.")
            return ExitCode.LLM_INVALID
        clips = selector.snap_clips(result, t, s["clips"], s["min_s"], s["max_s"])
        if clips:  # never cache an empty result — the user may retry with different bounds
            util.write_json(clips_path, [c.model_dump() for c in clips])
    if not clips:
        print("error: no usable clips after boundary snapping — try widening --min/--max.")
        return ExitCode.LLM_INVALID
    print(f"        {len(clips)} clips selected ({fmt_dur(time.perf_counter() - t0)}):")
    print_clip_table(clips)
    if args.dry_run:
        print(f"\ndry run complete — no video rendered. Total {fmt_dur(time.perf_counter() - t_all)}.")
        return ExitCode.OK

    # ---- S4: render ------------------------------------------------------------
    print(f"Rendering {len(clips)} clips ({s['ratio']}, {s['style']} crop, "
          f"captions {'on' if s['captions'] else 'off'})…")
    t0 = time.perf_counter()
    outdir = Path(s["outdir"]).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    out_w, out_h = RATIOS[s["ratio"]]
    fps = pr.fps or 30.0
    if s["fast"]:
        print("fast draft mode: keyframe-snapped stream copies — no reframe, no captions.")
    for c in clips:
        suffix = "__draft" if s["fast"] else ""
        out_file = outdir / f"clip_{c.id:02d}__{util.slugify(c.title)}{suffix}.mp4"
        if s["fast"]:
            try:
                media.render_clip(
                    media.build_draft_cmd(str(video), c.start, c.duration, str(out_file)))
            except Exception as e:  # noqa: BLE001
                print(f"error: draft render failed for clip {c.id:02d}: {e}")
                return ExitCode.GENERIC
            print(f"  OK clip {c.id:02d} -> {out_file.name} ({c.duration:.1f} s, draft)")
            continue
        ass_path = None
        cards: list[dict] = []
        crop_x = None
        if s["style"] == "face":
            try:
                crop_x = reframe.smart_crop_x(str(video), c.start, c.duration,
                                              pr.width, pr.height, out_w, out_h)
            except reframe.FaceTrackError as e:
                print(f"error: {e}")
                return ExitCode.GENERIC
            if crop_x is None:
                print(f"  note: clip {c.id:02d}: weak face signal — center-crop fallback")
        if s["captions"]:
            cards = window_cards(t, c.start, c.end, fps, max_words=s["caption_words"])
            ass_path = cdir / f"captions_{c.id:02d}.ass"
            captions.render_ass(cards, str(ass_path), out_w=out_w, out_h=out_h,
                                font=s["caption_font"], font_size=s["caption_size"],
                                pos=s["caption_pos"], color=s["caption_color"])
        try:
            cmd = media.build_render_cmd(
                str(video), c.start, c.duration, str(out_file),
                out_w=out_w, out_h=out_h,
                ass_file=str(ass_path) if ass_path else None,
                style="center" if s["style"] == "face" else s["style"],
                preset=s["preset"], crf=s["crf"], crop_x=crop_x)
            media.render_clip(cmd, cwd=str(cdir))
        except Exception as e:  # noqa: BLE001
            print(f"error: render failed for clip {c.id:02d}: {e}")
            return ExitCode.GENERIC
        if s["srt"] and cards:
            captions.write_srt(cards, str(out_file.with_suffix(".srt")))
        (outdir / f"clip_{c.id:02d}.txt").write_text(
            f"{c.title}\n\n{c.hook}\n\n{c.caption}\n\n{' '.join(c.hashtags)}".strip() + "\n",
            encoding="utf-8")
        print(f"  OK clip {c.id:02d} -> {out_file.name} ({c.duration:.1f} s)")
        if ass_path and not args.keep_work:
            ass_path.unlink(missing_ok=True)

    manifest_settings = {k: v for k, v in s.items() if isinstance(v, (str, int, float, bool))}
    if source_url:
        manifest_settings["source_url"] = source_url
    manifest = models.Manifest(
        source=source_url or video.name,
        generated_at=datetime.now().isoformat(timespec="seconds"),
        settings=manifest_settings,
        clips=clips)
    (outdir / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

    if not args.keep_work:
        (wav).unlink(missing_ok=True)  # transcript.json / clips.json stay as cache

    print(f"\nDone -> {outdir.resolve()}  ({len(clips)} clips, manifest.json, "
          f"{len(clips)} .txt)   Total {fmt_dur(time.perf_counter() - t_all)}")
    return ExitCode.OK


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    util.setup_logging(verbose=False)
    s = build_settings(args, load_config(Path("lightclip.toml")))
    t_all = time.perf_counter()

    if args.doctor:
        return run_doctor()
    if args.ui:
        from lightclip import ui_server
        ui_server.serve(port=args.port, open_browser=True)
        return ExitCode.OK
    if args.setup:
        return run_setup(args)
    if not args.video:
        print("error: no input given — pass a video file, an http(s) URL, a folder, or --doctor.")
        return ExitCode.GENERIC

    if fetch.is_url(args.video):
        return run_one(args.video, s, args)

    target = Path(args.video)
    if target.is_dir():
        videos = util.collect_videos(target)
        if not videos:
            print(f"error: no video files found in {target} "
                  f"(looking for {', '.join(sorted(util.VIDEO_EXTS))})")
            return ExitCode.GENERIC
        print(f"Batch mode: {len(videos)} video(s) in {target} "
              f"(one settings set for the folder, output per video)\n")
        failed: list[tuple[str, int]] = []
        for i, v in enumerate(videos, 1):
            s_i = dict(s)
            s_i["outdir"] = str(Path(s["outdir"]) / v.stem)
            print(f"=== [{i}/{len(videos)}] {v.name} ===")
            code = run_one(str(v), s_i, args)
            if code != ExitCode.OK:
                failed.append((v.name, int(code)))
            print()
        ok_count = len(videos) - len(failed)
        print(f"Batch summary: {ok_count}/{len(videos)} ok"
              + (f", {len(failed)} failed:" if failed else ""))
        for name, code in failed:
            print(f"  FAILED {name} (exit {code})")
        print(f"Total batch time {fmt_dur(time.perf_counter() - t_all)}")
        return ExitCode.OK if not failed else ExitCode.GENERIC

    if not target.exists():
        print(f"error: input file not found: {target}")
        return ExitCode.GENERIC
    return run_one(str(target), s, args)


def transcribe_stage(wav: Path, s: dict) -> Transcript:
    """Runs in a worker context so the model is released before rendering."""
    import gc

    from lightclip import asr
    t = asr.transcribe(str(wav), model_size=s["asr_model"], language=s["lang"], vad=True)
    gc.collect()
    return t


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="lightclip",
        description="Turn long videos into captioned vertical shorts (local, BYOK).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
               "  python lightclip.py episode-42.mp4\n"
               "  python lightclip.py episode-42.mp4 --clips 12 --min 15 --max 45\n"
               "  python lightclip.py episode-42.mp4 --style blur --platform tiktok\n"
               "  python lightclip.py episode-42.mp4 --dry-run --no-captions --srt\n"
               "  python lightclip.py \"https://youtu.be/...\"   # URL input — engine downloads via yt-dlp\n"
               "  python lightclip.py ./videos-folder          # batch: every video inside\n"
               "  python lightclip.py --ui                 # web UI: drag, drop, clip\n"
               "  python lightclip.py --doctor                 # verify the installation\n")
    p.add_argument("video", nargs="?", default=None,
                   help="input video file, http(s) URL, or a FOLDER of videos (batch mode)")
    p.add_argument("--doctor", action="store_true", help="verify the installation and exit")
    p.add_argument("--ui", action="store_true", help="launch the local web UI (opens your browser)")
    p.add_argument("--port", type=int, default=8765, help="port for the web UI (default 8765)")
    p.add_argument("--setup", action="store_true",
                   help="interactive onboarding: persist your API key, verify, done")
    p.add_argument("--setup-provider", choices=["anthropic", "openai"],
                   help="(setup) provider; skips the interactive prompt")
    p.add_argument("--setup-key", help="(setup) API key — prefer interactive input "
                                       "(flag values land in shell history)")
    p.add_argument("--setup-base-url", help="(setup) gateway base URL, e.g. https://host/v1")
    p.add_argument("--setup-model", help="(setup) default model id for your provider")
    p.add_argument("--clips", type=int, help="number of clips (default 8)")
    p.add_argument("--min", dest="min_s", type=float, help="min clip seconds (default 20)")
    p.add_argument("--max", dest="max_s", type=float, help="max clip seconds (default 60)")
    p.add_argument("--ratio", choices=list(RATIOS), help="output aspect (default 9:16)")
    p.add_argument("--style", choices=["center", "blur", "face"],
                   help="reframe style: center / blur / face (YuNet smart-crop)")
    p.add_argument("--platform", choices=list(PLATFORMS),
                   help="preset: sets ratio + duration cap")
    p.add_argument("--captions", action=argparse.BooleanOptionalAction,
                   help="burn pop-in captions (default on)")
    p.add_argument("--caption-font", help='caption font (default "Arial Black")')
    p.add_argument("--caption-size", type=int,
                   help="caption font size px (default 64 at 1080-wide reference)")
    p.add_argument("--caption-pos", type=float,
                   help="caption vertical center 0-1 (default 0.72)")
    p.add_argument("--caption-color", help="caption color hex RGB (default FFFFFF)")
    p.add_argument("--caption-words", type=int, help="max words per caption card (default 4)")
    p.add_argument("--srt", action="store_true", help="also write .srt sidecars")
    p.add_argument("--lang", help="force ASR language (ISO code); default auto-detect")
    p.add_argument("--asr-model", choices=["tiny", "base", "small", "medium"],
                   help="faster-whisper model (default small)")
    p.add_argument("--provider", choices=["anthropic", "openai"],
                   help="LLM provider (default: auto-detect from env)")
    p.add_argument("--llm-model", help="model id override")
    p.add_argument("--heuristic", action="store_true",
                   help="offline selection: speech-density heuristic — no API key, no cost")
    p.add_argument("--fast", action="store_true", help="keyframe-snapped draft cuts")
    p.add_argument("--dry-run", action="store_true", help="selection only, no rendering")
    p.add_argument("--outdir", help="output folder (default ./shorts; batch writes <outdir>/<stem>/)")
    p.add_argument("--workdir", help="cache folder (default ./work)")
    p.add_argument("--keep-work", action="store_true", help="keep intermediates (audio.wav)")
    p.add_argument("--verbose", action="store_true", help="debug logging")
    return p.parse_args(argv)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted — cached stages let you resume with the same command.")
        sys.exit(130)
