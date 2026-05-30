#!/usr/bin/env python3
"""Local web interface for make_video.

Run:
    python3 app.py

Then open http://localhost:8000 in your browser, choose one audio file and your
16:9 images, click "Create video", watch the progress bar, then download the MP4.

Stdlib only -- no pip packages. Requires ffmpeg (brew install ffmpeg). Binds to
localhost only, so it is not exposed to your network.

Rendered videos are kept under ./renders/<job_id>/output.mp4 with a job.json so
that download links keep working even after the server is restarted.
"""

from __future__ import annotations

import cgi
import json
import os
import re
import shutil
import subprocess
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from make_video import (
    DEFAULT_HEIGHT,
    IMAGE_EXTS,
    RESOLUTION_WIDTHS,
    SECONDS_PER_IMAGE,
    ZOOM_MODES,
    build_concat_file,
    build_ffmpeg_cmd,
    check_ffmpeg,
    get_audio_duration,
    get_images,
)

HERE = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", "8000"))

# Persistent render storage: renders/<job_id>/output.mp4 + job.json
RENDERS_DIR = HERE / "renders"
# job_id is always a uuid4 hex string; validate before using it in a path.
JOB_ID_RE = re.compile(r"^[0-9a-f]{32}$")

# In-memory job registry: job_id -> dict. Guarded by JOBS_LOCK.
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def _hhmmss_to_seconds(value: str) -> float:
    """Parse ffmpeg's ``out_time=HH:MM:SS.micro`` into seconds."""
    hours, minutes, seconds = value.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _safe_name(name: str) -> str:
    """Strip any directory components from an uploaded filename."""
    base = os.path.basename(name.replace("\\", "/"))
    return base or "file"


def _content_disposition(disposition: str, filename: str) -> str:
    """Build a Content-Disposition value safe for spaces/commas/unicode.

    Provides an ASCII fallback plus an RFC 6266 ``filename*`` for the real name.
    """
    ascii_name = filename.replace('"', "").replace("\\", "")
    ascii_name = ascii_name.encode("ascii", "ignore").decode("ascii").strip()
    ascii_name = ascii_name or "video.mp4"
    encoded = quote(filename, safe="")
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"


def _job_from_meta(job_id: str, meta: dict) -> dict:
    """Build an in-memory job dict from persisted job.json metadata."""
    output = RENDERS_DIR / job_id / meta.get("output", "output.mp4")
    status = meta.get("status", "done")
    return {
        "status": status,
        "percent": 100.0 if status == "done" else 0.0,
        "error": meta.get("error"),
        "summary": meta.get("summary"),
        "output": str(output),
        "download_name": meta.get("download_name", "video.mp4"),
    }


def _write_job_meta(job_dir: Path, job: dict) -> None:
    """Persist a job's metadata next to its output so it survives restarts."""
    meta = {
        "status": job.get("status"),
        "error": job.get("error"),
        "summary": job.get("summary"),
        "download_name": job.get("download_name", "video.mp4"),
        "output": os.path.basename(job.get("output", "output.mp4")),
    }
    try:
        (job_dir / "job.json").write_text(json.dumps(meta), encoding="utf-8")
    except OSError:
        pass


def _load_jobs_from_disk() -> None:
    """Populate JOBS from any previously rendered jobs under RENDERS_DIR."""
    if not RENDERS_DIR.is_dir():
        return
    for entry in RENDERS_DIR.iterdir():
        if not entry.is_dir() or not JOB_ID_RE.match(entry.name):
            continue
        meta_file = entry / "job.json"
        if not meta_file.is_file():
            continue
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        JOBS[entry.name] = _job_from_meta(entry.name, meta)


def _resolve_job(job_id: str) -> dict | None:
    """Look up a job in memory, falling back to its persisted job.json on disk."""
    if not JOB_ID_RE.match(job_id):
        return None
    job = JOBS.get(job_id)
    if job is not None:
        return job
    meta_file = RENDERS_DIR / job_id / "job.json"
    if not meta_file.is_file():
        return None
    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    job = _job_from_meta(job_id, meta)
    with JOBS_LOCK:
        JOBS[job_id] = job
    return job


def _render_job(
    job_id: str,
    job_dir: Path,
    inputs_dir: Path,
    audio_path: Path,
    img_dir: Path,
    output: Path,
    zoom_mode: str,
    fade: float,
    normalize: bool,
    height: int,
) -> None:
    """Background worker: build the concat file and render with progress updates."""
    job = JOBS[job_id]
    concat = job_dir / "_concat.txt"
    log_path = job_dir / "ffmpeg.log"
    try:
        audio_duration = get_audio_duration(audio_path)
        images = get_images(img_dir)
        if not images:
            raise RuntimeError("No valid images were uploaded.")
        entries, loops = build_concat_file(images, audio_duration, concat)

        with JOBS_LOCK:
            job["status"] = "rendering"
            job["percent"] = 0.0

        # Reuse the exact CLI encode settings, but with two changes that prevent a
        # pipe deadlock on long renders:
        #   1. Drop "-stats" and add "-nostats" so ffmpeg does NOT stream a stats
        #      line to stderr (otherwise it spams stderr the whole render).
        #   2. Send stderr to a file, not a PIPE, so it can never fill its buffer
        #      and block ffmpeg (which froze renders at 0% CPU mid-way).
        # Progress comes from "-progress pipe:1" on stdout, which we drain live.
        cmd = build_ffmpeg_cmd(
            concat, audio_path, output, audio_duration,
            zoom_mode, fade, normalize, height,
        )
        cmd = [c for c in cmd if c != "-stats"]
        cmd = [cmd[0], "-progress", "pipe:1", "-nostats"] + cmd[1:]

        with open(log_path, "w") as logfile:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=logfile, text=True
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.strip()
                if line.startswith("out_time=") and audio_duration > 0:
                    try:
                        seconds = _hhmmss_to_seconds(line.split("=", 1)[1])
                    except ValueError:
                        continue
                    pct = min(99.0, seconds / audio_duration * 100)
                    with JOBS_LOCK:
                        job["percent"] = max(job["percent"], pct)
                elif line == "progress=end":
                    break
            proc.wait()

        if proc.returncode != 0:
            stderr = log_path.read_text(errors="replace").strip()
            tail = "\n".join(stderr.splitlines()[-12:])
            raise RuntimeError(tail or "ffmpeg failed to render the video.")

        size_mb = output.stat().st_size / (1024 * 1024)
        with JOBS_LOCK:
            job.update(
                status="done",
                percent=100.0,
                summary={
                    "duration": round(audio_duration, 1),
                    "images": len(images),
                    "slots": entries,
                    "loops": round(loops, 1),
                    "size_mb": round(size_mb, 1),
                    "seconds_per_image": SECONDS_PER_IMAGE,
                    "resolution": f"{RESOLUTION_WIDTHS[height]}x{height}",
                },
            )
        _write_job_meta(job_dir, job)
    except Exception as err:  # surface any failure to the UI
        with JOBS_LOCK:
            job.update(status="error", error=str(err))
        _write_job_meta(job_dir, job)
    finally:
        # Keep only output.mp4 + job.json; drop the bulky inputs and temp files.
        for path in (concat, log_path):
            try:
                path.unlink()
            except OSError:
                pass
        shutil.rmtree(inputs_dir, ignore_errors=True)


class Handler(BaseHTTPRequestHandler):
    """Routes: GET / , GET /progress/<id> , GET /file/<id> , POST /render."""

    def log_message(self, *args) -> None:  # keep the console quiet
        pass

    # -- helpers -----------------------------------------------------------
    def _send_json(self, obj: dict, code: int = 200) -> None:
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # -- GET ---------------------------------------------------------------
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            try:
                html = (HERE / "index.html").read_bytes()
            except OSError:
                return self.send_error(500, "index.html not found")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return

        if path.startswith("/progress/"):
            job = _resolve_job(path[len("/progress/"):])
            if job is None:
                return self._send_json({"error": "unknown job"}, 404)
            with JOBS_LOCK:
                payload = {
                    "status": job.get("status"),
                    "percent": round(job.get("percent", 0.0), 1),
                    "error": job.get("error"),
                    "summary": job.get("summary"),
                    "download_name": job.get("download_name"),
                }
            return self._send_json(payload)

        if path.startswith("/file/"):
            return self._serve_file(
                path[len("/file/"):], "dl" in parse_qs(parsed.query)
            )

        self.send_error(404)

    def _serve_file(self, job_id: str, as_download: bool) -> None:
        job = _resolve_job(job_id)
        if job is None or job.get("status") != "done":
            return self.send_error(404, "Video not found")
        output = Path(job["output"])
        if not output.is_file():
            return self.send_error(404, "Video file missing")

        disposition = "attachment" if as_download else "inline"
        filename = job.get("download_name", "video.mp4")
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(output.stat().st_size))
        self.send_header(
            "Content-Disposition", _content_disposition(disposition, filename)
        )
        self.end_headers()
        with open(output, "rb") as handle:
            shutil.copyfileobj(handle, self.wfile)

    # -- POST --------------------------------------------------------------
    def do_POST(self) -> None:
        if urlparse(self.path).path != "/render":
            return self.send_error(404)

        ctype = self.headers.get("Content-Type", "")
        if not ctype.startswith("multipart/form-data"):
            return self._send_json({"error": "expected multipart/form-data"}, 400)

        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": ctype,
                "CONTENT_LENGTH": self.headers.get("Content-Length", "0"),
            },
        )

        audio_item = None
        image_items = []
        for item in form.list or []:
            if not getattr(item, "filename", None):
                continue
            if item.name == "audio":
                audio_item = item
            elif item.name == "images":
                image_items.append(item)

        if audio_item is None:
            return self._send_json({"error": "Please choose an audio file."}, 400)
        if not image_items:
            return self._send_json({"error": "Please choose at least one image."}, 400)

        RENDERS_DIR.mkdir(exist_ok=True)
        job_id = uuid.uuid4().hex
        job_dir = RENDERS_DIR / job_id
        inputs_dir = job_dir / "inputs"
        img_dir = inputs_dir / "images"
        img_dir.mkdir(parents=True)

        audio_name = _safe_name(audio_item.filename)
        audio_path = inputs_dir / ("audio" + Path(audio_name).suffix.lower())
        with open(audio_path, "wb") as handle:
            handle.write(audio_item.file.read())

        saved_images = 0
        rejected = []
        for item in image_items:
            name = _safe_name(item.filename)
            if Path(name).suffix.lower() not in IMAGE_EXTS:
                rejected.append(name)
                continue
            with open(img_dir / name, "wb") as handle:
                handle.write(item.file.read())
            saved_images += 1

        if saved_images == 0:
            shutil.rmtree(job_dir, ignore_errors=True)
            exts = ", ".join(sorted(IMAGE_EXTS))
            detail = ""
            if rejected:
                shown = ", ".join(rejected[:6])
                more = f" (+{len(rejected) - 6} more)" if len(rejected) > 6 else ""
                detail = f" These aren't supported: {shown}{more}."
            return self._send_json(
                {"error": f"No supported images were uploaded.{detail} Supported: {exts}."},
                400,
            )

        zoom_mode = form.getfirst("zoom", "alternate")
        if zoom_mode not in ZOOM_MODES:
            zoom_mode = "alternate"
        try:
            fade = max(0.0, float(form.getfirst("fade", "1")))
        except ValueError:
            fade = 1.0
        normalize = form.getfirst("normalize", "1") not in ("0", "false", "off", "")
        try:
            height = int(str(form.getfirst("resolution", DEFAULT_HEIGHT)).rstrip("p"))
        except ValueError:
            height = DEFAULT_HEIGHT
        if height not in RESOLUTION_WIDTHS:
            height = DEFAULT_HEIGHT

        output = job_dir / "output.mp4"
        JOBS[job_id] = {
            "status": "queued",
            "percent": 0.0,
            "error": None,
            "summary": None,
            "output": str(output),
            "download_name": (Path(audio_name).stem or "video") + ".mp4",
        }
        threading.Thread(
            target=_render_job,
            args=(job_id, job_dir, inputs_dir, audio_path, img_dir, output,
                  zoom_mode, fade, normalize, height),
            daemon=True,
        ).start()
        return self._send_json({"job_id": job_id})


def main() -> int:
    """Start the local server and (unless NO_OPEN is set) open a browser."""
    check_ffmpeg()
    RENDERS_DIR.mkdir(exist_ok=True)
    _load_jobs_from_disk()

    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as err:
        print(f"Could not start the server on port {PORT}: {err}")
        print(f"Another program may be using it. Try a different port:")
        print(f"    PORT=8080 python3 app.py")
        return 1

    url = f"http://{HOST}:{PORT}"
    print(f"make_video web UI running at {url}")
    print("Press Ctrl+C to stop.")
    if not os.environ.get("NO_OPEN"):
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
