"""LightClip local web UI backend (Python stdlib only).

Security model
--------------
* Loopback only: the server binds to 127.0.0.1, so it is unreachable from
  other machines on the network.
* Minimal content surface: the only page served is the single UI file
  (``lightclip/web/index.html``, read fresh on every request so edits
  hot-reload). ``/files/<relpath>`` serves a file only when its
  ``os.path.realpath`` resolves inside an allowlist of output directories
  (the standard ``shorts_*`` / ``bench_out`` folders plus the current job's
  outdir); realpath-prefix checks defeat ``../`` traversal. Uploaded files
  land in ``work/uploads``, which is deliberately NOT in that allowlist.
* Subprocess isolation: jobs run the LightClip CLI (``lightclip.py``) as a
  subprocess with the user's own environment (only ``PYTHONIOENCODING=utf-8``
  is added, so the server's UTF-8 log decoding matches what the CLI writes
  on Windows pipes). The server itself never reads, stores, or proxies API
  keys — it only reports whether one is present in the environment.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]   # build/ — holds the code
PROJECT = ROOT.parent                        # project root — holds outputs, caches, docs
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # lets `import lightclip` work on direct execution

INDEX_HTML = ROOT / "lightclip" / "web" / "index.html"

UPLOAD_DIR = PROJECT / "work" / "uploads"
UPLOAD_CHUNK = 256 * 1024  # stream uploads/downloads to disk/socket in 256 KB blocks
FILE_CHUNK = 256 * 1024
MAX_LOG_LINES = 80  # full log kept for the UI
STATUS_LINES = 40   # /api/status returns only the last 40 lines

CONTENT_TYPES = {".mp4": "video/mp4", ".json": "application/json", ".txt": "text/plain"}

def _job_env() -> dict:
    """os.environ + user-registry fallback for missing variables.

    A long-lived server inherits a stale parent environment; user-scope variables
    written later (e.g. the gateway key persisted by --setup) are read from
    HKCU\\Environment so UI-launched jobs always see the user's config.
    """
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                i = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(k, i)
                    except OSError:
                        break
                    i += 1
                    if name and (name not in env or not env[name]):
                        env[name] = value
        except OSError:
            pass
    return env


STATE: dict = {
    "proc": None,          # subprocess.Popen | None
    "lines": [],           # stripped CLI log lines (last 80 kept)
    "stage": 0,            # 0 idle .. 5 rendering
    "stage_label": "Idle",
    "clips_done": 0,
    "clips_total": None,   # int | None (set from the 'Rendering N clips' line)
    "running": False,
    "done": False,
    "exit_code": None,     # int | None
    "outdir": None,        # str | None (set from the CLI's 'Done ->' line)
    "error": None,         # str | None (set from the CLI's 'error:' lines)
    "started_at": time.time(),
}
STATE_LOCK = threading.Lock()

# The only directories /files/ may serve from; STATE["outdir"] is added
# dynamically once a job reports where it wrote.
_ALLOWED_ROOTS: list[Path] = [
    PROJECT / "shorts",
    PROJECT / "bench_out",
    PROJECT / "shorts_fast",
    PROJECT / "shorts_smoke",
    PROJECT / "shorts_cli",
    PROJECT / "shorts_live",
    PROJECT / "shorts_url",
    PROJECT / "shorts_face",
    PROJECT / "shorts_yt",
    PROJECT / "shorts_movie",
    PROJECT / "shorts_batch",
    PROJECT / "shorts_ambient",
]


def _lightclip_version() -> str:
    try:
        import lightclip  # sys.path already contains ROOT (see module top)
    except Exception:  # noqa: BLE001 - the UI must come up even if the import fails
        return "unknown"
    return str(getattr(lightclip, "__version__", "unknown"))


def _is_url(source: str) -> bool:
    return source.lower().startswith(("http://", "https://"))


def _sanitize_filename(name: str) -> str:
    """Reduces an unquoted upload name to a safe Windows basename ('' = reject)."""
    name = os.path.basename(name.replace("\\", "/")).strip()
    cleaned = "".join("_" if (c in '<>:"|?*' or ord(c) < 32) else c for c in name)
    return cleaned.strip(" .")[:120]


def _clip_id_from_name(name: str) -> int | None:
    m = re.match(r"clip_(\d+)", name)
    return int(m.group(1)) if m else None


def _build_cmd(source: str, body: dict) -> list[str]:
    """Maps /api/start JSON fields onto lightclip.py flags (pure, unit-testable)."""
    cmd = [sys.executable, str(ROOT / "lightclip.py"), source]
    clips = body.get("clips")
    if isinstance(clips, int) and not isinstance(clips, bool):
        cmd += ["--clips", str(clips)]
    for key, flag in (("min_s", "--min"), ("max_s", "--max")):
        val = body.get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            cmd += [flag, str(val)]
    style = body.get("style")
    if isinstance(style, str) and style.strip():
        cmd += ["--style", style.strip()]
    # BooleanOptionalAction on the CLI accepts both spellings; absent = on (CLI default).
    cmd += ["--no-captions"] if body.get("captions") is False else ["--captions"]
    asr_model = body.get("asr_model")
    if isinstance(asr_model, str) and asr_model.strip():
        cmd += ["--asr-model", asr_model.strip()]
    if body.get("heuristic"):
        cmd += ["--heuristic"]
    if body.get("fast"):
        cmd += ["--fast"]
    lang = body.get("lang")
    if isinstance(lang, str) and lang.strip():
        cmd += ["--lang", lang.strip()]
    outdir = body.get("outdir")
    if isinstance(outdir, str) and outdir.strip():
        cmd += ["--outdir", outdir.strip()]
    return cmd


def _reset_state_locked() -> None:
    """Fresh per-job STATE. Lock held."""
    STATE.update({
        "proc": None,
        "lines": [],
        "stage": 0,
        "stage_label": "Starting",
        "clips_done": 0,
        "clips_total": None,
        "running": True,
        "done": False,
        "exit_code": None,
        "outdir": None,
        "error": None,
        "started_at": time.time(),
    })


def _apply_parse_rules_locked(line: str) -> None:
    """Applies the CLI-progress parse rules to one raw (rstripped) line. Lock held.

    Matching uses the raw line (not the stripped copy) because '  OK clip ' is an
    exact two-space prefix in lightclip.py's output.
    """
    if line.startswith("[1/4]"):
        STATE["stage"] = 1
        STATE["stage_label"] = "Probing"
    elif line.startswith("[2/4]"):
        STATE["stage"] = 2
        STATE["stage_label"] = "Extracting audio"
    elif line.startswith("[3/4]"):
        STATE["stage"] = 3
        STATE["stage_label"] = "Transcribing"
    elif line.startswith("[4/4]"):
        STATE["stage"] = 4
        STATE["stage_label"] = "Selecting"
    elif line.startswith("Rendering "):
        STATE["stage"] = 5
        STATE["stage_label"] = "Rendering"
        m = re.match(r"Rendering (\d+) clips", line)
        if m:
            STATE["clips_total"] = int(m.group(1))
    elif line.startswith("fast draft mode:"):
        STATE["stage"] = 5
        STATE["stage_label"] = "Rendering (draft)"
    elif line.startswith("  OK clip "):
        STATE["clips_done"] += 1
    elif line.startswith("Done ->"):
        # 'Done -> <outdir>  (N clips, manifest.json, N .txt)   Total ...'
        STATE["outdir"] = line[len("Done ->"):].lstrip().split("  (")[0].strip() \
            or STATE["outdir"]
    elif line.startswith("error:"):
        STATE["error"] = line.strip()


def _reader(proc: subprocess.Popen) -> None:
    """Daemon thread: tails the CLI subprocess stdout into STATE until EOF."""
    stream = proc.stdout
    try:
        if stream is not None:
            for raw in stream:
                line = raw.rstrip("\r\n")  # keep leading spaces for '  OK clip '
                text = line.strip()
                with STATE_LOCK:
                    _apply_parse_rules_locked(line)
                    STATE["lines"].append(text)
                    if len(STATE["lines"]) > MAX_LOG_LINES:
                        del STATE["lines"][: len(STATE["lines"]) - MAX_LOG_LINES]
    except (ValueError, OSError):
        pass  # stream closed / process gone — finalization happens below
    finally:
        try:
            exit_code: int | None = proc.wait()
        except Exception:  # noqa: BLE001 - the reader must never crash the UI
            exit_code = None
        try:
            if stream is not None and not stream.closed:
                stream.close()
        except Exception:  # noqa: BLE001
            pass
        with STATE_LOCK:
            STATE["exit_code"] = exit_code
            STATE["running"] = False
            STATE["done"] = True
            STATE["proc"] = None


def _allowed_roots_real() -> list[tuple[Path, str]]:
    """(root, realpath) pairs, including the dynamic STATE['outdir'] entry."""
    roots = list(_ALLOWED_ROOTS)
    with STATE_LOCK:
        outdir = STATE["outdir"]
    if outdir:
        roots.append(Path(outdir))
    pairs: list[tuple[Path, str]] = []
    for r in roots:
        try:
            pairs.append((r, os.path.realpath(str(r))))
        except OSError:
            continue
    return pairs


def _relpath_under_allowed_roots(path: Path) -> str | None:
    real = os.path.realpath(str(path))
    for _root, real_root in _allowed_roots_real():
        if real == real_root or real.startswith(real_root + os.sep):
            return os.path.relpath(real, real_root).replace(os.sep, "/")
    return None


class Handler(BaseHTTPRequestHandler):
    """Routes; HTTP/1.1 with exact Content-Length on every body response."""

    server_version = "LightClipUI/2.0"
    protocol_version = "HTTP/1.1"
    _responded = False  # guards the 500 fallback against double-responding

    # ---- plumbing ----------------------------------------------------------

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: A002
        """Silence the default per-request console logging."""
        return

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body:
            self.wfile.write(body)
        self._responded = True

    def _send_json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _send_204(self) -> None:
        self.send_response(204)
        self.end_headers()
        self._responded = True

    def _send_file(self, path: str, ctype: str) -> None:
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Accept-Ranges", "none")
        self.end_headers()
        self._responded = True
        with open(path, "rb") as f:
            while True:
                chunk = f.read(FILE_CHUNK)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return b""
        return self.rfile.read(length) if length > 0 else b""

    def _drain_body(self) -> None:
        """Reads and discards a posted body so HTTP/1.1 keep-alive stays in sync."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        while length > 0:
            chunk = self.rfile.read(min(UPLOAD_CHUNK, length))
            if not chunk:
                break
            length -= len(chunk)

    # ---- dispatch ----------------------------------------------------------

    def do_GET(self) -> None:
        self._responded = False
        try:
            self._route_get()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as e:  # noqa: BLE001 - keep the UI alive, report 500
            if not self._responded:
                try:
                    self._send_json(500, {"ok": False, "error": f"server error: {e}"})
                except Exception:  # noqa: BLE001
                    self.close_connection = True
            else:
                self.close_connection = True

    def _route_get(self) -> None:
        path = urlsplit(self.path).path
        if path == "/":
            self._serve_index()
        elif path == "/api/info":
            self._serve_info()
        elif path == "/api/status":
            self._serve_status()
        elif path == "/api/results":
            self._serve_results()
        elif path == "/favicon.ico":
            self._send_204()
        elif path.startswith("/files/"):
            self._serve_file(unquote(path[len("/files/"):]))
        else:
            self._send_json(404, {"ok": False, "error": "not found"})

    # ---- GET routes --------------------------------------------------------

    def _serve_index(self) -> None:
        try:
            body = INDEX_HTML.read_bytes()  # fresh each request → edits hot-reload
        except OSError:
            self._send(500, b"index.html missing", "text/plain; charset=utf-8")
            return
        self._send(200, body, "text/html; charset=utf-8")

    def _serve_info(self) -> None:
        with STATE_LOCK:
            busy = bool(STATE["running"])
        env = _job_env()  # registry fallback: a long-lived server may predate --setup
        self._send_json(200, {
            "version": _lightclip_version(),
            "key_set": bool(env.get("ANTHROPIC_API_KEY")
                            or env.get("OPENAI_API_KEY")),
            "base_url": env.get("OPENAI_BASE_URL") or env.get("ANTHROPIC_BASE_URL"),
            "model": env.get("OPENAI_MODEL") or env.get("ANTHROPIC_MODEL"),
            "busy": busy,
            "outdir_default": str(PROJECT / "shorts"),
        })

    def _serve_status(self) -> None:
        with STATE_LOCK:
            snapshot = {
                "running": STATE["running"],
                "done": STATE["done"],
                "exit_code": STATE["exit_code"],
                "stage": STATE["stage"],
                "stage_label": STATE["stage_label"],
                "clips_done": STATE["clips_done"],
                "clips_total": STATE["clips_total"],
                "error": STATE["error"],
                "outdir": STATE["outdir"],
                "lines": list(STATE["lines"][-STATUS_LINES:]),
            }
        self._send_json(200, snapshot)

    def _serve_results(self) -> None:
        with STATE_LOCK:
            outdir_raw = STATE["outdir"]
        outdir = Path(outdir_raw) if outdir_raw else PROJECT / "shorts"
        manifest_clips: list = []
        try:
            data = json.loads((outdir / "manifest.json").read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("clips"), list):
                manifest_clips = data["clips"]
        except (OSError, ValueError):
            manifest_clips = []  # no manifest (yet) → empty results
        by_id = {c.get("id"): c for c in manifest_clips
                 if isinstance(c, dict) and "id" in c}
        clips: list[dict] = []
        if outdir.is_dir():
            for mp4 in sorted(outdir.glob("*.mp4")):
                cid = _clip_id_from_name(mp4.name)
                meta = by_id.get(cid)
                try:
                    size_mb = round(mp4.stat().st_size / (1024 * 1024), 2)
                except OSError:
                    size_mb = None
                rel = _relpath_under_allowed_roots(mp4)
                clips.append({
                    "id": cid,
                    "title": meta.get("title") if meta else mp4.stem,
                    "duration": meta.get("duration") if meta else None,
                    "score": meta.get("virality_score") if meta else None,
                    "file": mp4.name,
                    "size_mb": size_mb,
                    "url": f"/files/{rel}" if rel else None,
                })
        self._send_json(200, {"outdir": str(outdir), "clips": clips})

    def _serve_file(self, relpath: str) -> None:
        rel = relpath.replace("\\", "/").lstrip("/")
        if not rel:
            self._send_json(403, {"ok": False, "error": "forbidden"})
            return
        inside = False
        found: str | None = None
        for root, real_root in _allowed_roots_real():
            real = os.path.realpath(str(root / rel))
            if real == real_root or real.startswith(real_root + os.sep):
                inside = True
                if os.path.isfile(real):
                    found = real
                    break
        if found is None:
            if inside:
                self._send_json(404, {"ok": False, "error": "not found"})
            else:
                self._send_json(403, {"ok": False, "error": "forbidden"})
            return
        ext = os.path.splitext(found)[1].lower()
        self._send_file(found, CONTENT_TYPES.get(ext, "application/octet-stream"))

    # ---- POST routes -------------------------------------------------------

    def do_POST(self) -> None:
        self._responded = False
        try:
            self._route_post()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as e:  # noqa: BLE001 - keep the UI alive, report 500
            if not self._responded:
                try:
                    self._send_json(500, {"ok": False, "error": f"server error: {e}"})
                except Exception:  # noqa: BLE001
                    self.close_connection = True
            else:
                self.close_connection = True

    def _route_post(self) -> None:
        path = urlsplit(self.path).path
        if path == "/api/upload":
            self._handle_upload()
        elif path == "/api/start":
            self._handle_start()
        else:
            self._drain_body()
            self._send_json(404, {"ok": False, "error": "not found"})

    def _handle_upload(self) -> None:
        raw_name = self.headers.get("X-Filename") or ""
        safename = _sanitize_filename(unquote(raw_name))
        if not safename:
            self._drain_body()
            self._send_json(400, {"ok": False, "error": "missing or empty X-Filename header"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            self._send_json(400, {"ok": False, "error": "empty upload body"})
            return
        try:
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            dest = UPLOAD_DIR / f"{int(time.time())}_{safename}"
            remaining = length
            with open(dest, "wb") as out:
                while remaining > 0:
                    chunk = self.rfile.read(min(UPLOAD_CHUNK, remaining))
                    if not chunk:
                        break  # client hung up mid-upload
                    out.write(chunk)
                    remaining -= len(chunk)
        except OSError as e:
            self._send_json(500, {"ok": False, "error": f"could not store upload: {e}"})
            return
        if remaining > 0:
            self._send_json(400, {"ok": False,
                                  "error": f"incomplete upload: got {length - remaining} "
                                           f"of {length} bytes"})
            return
        self._send_json(200, {"ok": True, "path": str(dest.resolve())})

    def _handle_start(self) -> None:
        raw = self._read_body()
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
            if not isinstance(body, dict):
                raise ValueError("JSON body must be an object")
        except (UnicodeDecodeError, ValueError) as e:
            self._send_json(400, {"ok": False, "error": f"invalid JSON body: {e}"})
            return
        source = body.get("source")
        if not isinstance(source, str) or not source.strip():
            self._send_json(400, {"ok": False, "error": "missing required field: source"})
            return
        source = source.strip()
        with STATE_LOCK:
            busy = bool(STATE["running"])
        if busy:
            self._send_json(409, {"ok": False, "error": "a job is already running"})
            return
        if not _is_url(source):
            if not os.path.isabs(source):
                source = str(PROJECT / source)  # relative sources resolve at the project root
            if not os.path.exists(source):
                self._send_json(400, {"ok": False, "error": f"source not found: {source}"})
                return
        with STATE_LOCK:
            # Re-check atomically with the reset so two concurrent starts
            # can never both spawn a job.
            if STATE["running"]:
                conflict = True
            else:
                conflict = False
                _reset_state_locked()
        if conflict:
            self._send_json(409, {"ok": False, "error": "a job is already running"})
            return
        cmd = _build_cmd(source, body)
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(PROJECT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=_job_env(),
            )
        except OSError as e:
            with STATE_LOCK:
                STATE["running"] = False
                STATE["done"] = True
                STATE["error"] = f"failed to start lightclip.py: {e}"
            self._send_json(500, {"ok": False, "error": f"failed to start lightclip.py: {e}"})
            return
        with STATE_LOCK:
            STATE["proc"] = proc
        threading.Thread(target=_reader, args=(proc,),
                         name="lightclip-log", daemon=True).start()
        self._send_json(200, {"ok": True})


def serve(port: int = 8765, open_browser: bool = True) -> None:
    """Starts the UI server on 127.0.0.1:<port> and blocks until Ctrl+C."""
    url = f"http://127.0.0.1:{port}"
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"LightClip UI running at {url}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        timer = threading.Timer(0.6, lambda: webbrowser.open(url))
        timer.daemon = True
        timer.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        # serve_forever has already left its loop here, so shutdown() returns
        # immediately instead of deadlocking on the same thread.
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    _port = 8765
    if len(sys.argv) > 1:
        try:
            _port = int(sys.argv[1])
        except ValueError:
            print(f"error: port must be an integer, got {sys.argv[1]!r}")
            raise SystemExit(2)
    serve(port=_port)
