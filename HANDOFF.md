# HANDOFF — make_video

> Onboarding doc for a fresh Claude Code session. Read this first.
> Last updated: 2026-05-29.

## What this project is

A **local macOS tool** that turns one audio file + a folder of 16:9 images into a
single **looping MP4**. Each image shows for 13 seconds, images cycle in numeric
filename order (1, 2, 3 …), and the video is trimmed to exactly the audio length.
Intended for making long-form "story" videos; **there is no upload step** — the user
uploads to YouTube manually.

Two front-ends, one engine:
- **CLI** — `make_video.py`
- **Local web UI** — `app.py` + `index.html` (reuses the CLI's functions)

**Status: complete and verified.** Both front-ends work end-to-end (see Verification).

## Environment (verified facts)

- Project root: `/Users/santipapmay/Youtube automation` (note the **space** in the path
  — quote it in shell commands).
- macOS (darwin). `ffmpeg` + `ffprobe` installed via Homebrew at `/opt/homebrew/bin`.
- Default `python3` is **3.9.6** (`/usr/bin/python3`, Apple system Python). Not 3.10+.
- **Zero Python dependencies** — stdlib only. `requirements.txt` is intentionally empty.

## Files

| File | Role |
|------|------|
| `make_video.py` | CLI + the core engine (all rendering logic lives here). |
| `app.py` | Local web server (stdlib `http.server`). Imports and reuses `make_video.py`. |
| `index.html` | Web UI. Self-contained (inline CSS/JS, no external assets, works offline). |
| `requirements.txt` | Empty (comment only). No `pip install` needed. |
| `README.md` | User-facing setup + usage for both CLI and web. |
| `HANDOFF.md` | This file. |
| `renders/` | Web output history: `renders/<job_id>/output.mp4` + `job.json` (auto-created). |

### `make_video.py` — functions
- `check_ffmpeg()` — exits with `brew install ffmpeg` message if ffmpeg/ffprobe missing.
- `_natural_key(path)` — sort key for **numeric** filename order (1,2,3…10,11, not 1,10,2).
- `get_audio_duration(audio) -> float` — via `ffprobe -show_entries format=duration -of json`.
- `get_images(folder) -> list[Path]` — supported images, absolute paths, natural-sorted.
- `build_concat_file(images, audio_duration, concat_path) -> (entries, loops)` — writes the
  ffmpeg concat-demuxer file.
- `_zoom_filter(mode, seconds, fps) -> str` — builds the zoompan chain for the Ken Burns
  zoom **and** a gentle alternating pan. Zoom/pan are driven by `mod(on, frames_per_image)`
  so they **reset per image** under the concat demuxer (plain `zoompan` drifts). Modes in
  `ZOOM_MODES`.
- `_video_filter(zoom_mode, audio_duration, fade)` / `_audio_filter(audio_duration, fade,
  normalize)` — assemble the `-vf` (zoom/pan + fade in/out) and `-af` (loudnorm to -14 LUFS
  + afade in/out) chains. `_clamp_fade` keeps fades ≤ half the clip.
- `build_ffmpeg_cmd(concat, audio, output, audio_duration, zoom_mode="alternate",
  fade=1.0, normalize=True)` — **shared** ffmpeg command (CLI + web).
- `create_video(..., zoom_mode, fade, normalize)` — runs it synchronously (CLI).
- `main() -> int` — arg parsing (`--zoom MODE`, `--fade SECONDS`, `--no-normalize`).
- Constants: `SECONDS_PER_IMAGE = 13`, `FPS = 30`, `ZOOM_AMOUNT = 0.12`, `PAN_AMOUNT = 0.5`,
  `FADE_SECONDS = 1.0`, `ZOOM_MODES = (alternate, in, out, inout, none)`,
  `IMAGE_EXTS = {.jpg,.jpeg,.png,.webp}`.

### `app.py` — web server
- Binds **`127.0.0.1` only** (not exposed to the network). Port from `PORT` env, default 8000.
- **Persistent jobs:** each render lives in `renders/<job_id>/` with `output.mp4` + a
  `job.json` (status, summary, download_name). In-memory `JOBS` (guarded by `JOBS_LOCK`) is
  a cache; on startup `_load_jobs_from_disk()` repopulates it, and `_resolve_job()` falls
  back to `job.json`. **So download links survive server restarts.** (`job_id` is a uuid4
  hex; validated with `JOB_ID_RE` before being used in a path → no traversal.)
- `_render_job(...)` — background thread: builds concat file, runs ffmpeg with
  `-progress pipe:1` and parses `out_time=` for a real %; on finish writes `job.json` and
  deletes the bulky `inputs/` + temp files (keeps `output.mp4` + `job.json`).
- Routes:
  - `GET /` → serves `index.html` (with `Cache-Control: no-store`).
  - `POST /render` → multipart upload (audio + images) via stdlib **`cgi.FieldStorage`**;
    saves to `renders/<job_id>/inputs/`, starts the render thread, returns `{"job_id": ...}`.
  - `GET /progress/<id>` → JSON `{status, percent, error, summary, download_name}`.
  - `GET /file/<id>` → MP4 inline (for `<video>` preview); `?dl=1` → attachment download.
    Filename derived from the audio stem (`a.m4a` → `a.mp4`); `Content-Disposition` uses an
    ASCII fallback + RFC 6266 `filename*` so spaces/commas/unicode are safe.
- `main()` — `check_ffmpeg()`, `_load_jobs_from_disk()`, start `ThreadingHTTPServer`
  (friendly message + exit 1 if the port is busy), auto-open browser unless `NO_OPEN` set.

### `index.html` — UI
Dark themed, single card: audio picker (one file) + images picker (multiple), a note, a
"Create video" button. On submit: `POST /render`, then polls `GET /progress/<id>` every
700ms, drives a progress bar, then shows a `<video>` preview + download button + summary.

## How to run

**CLI:**
```bash
python3 make_video.py <audio_file> <images_folder> <output_file>
# e.g.
python3 make_video.py audio.mp3 ./images ./output/story.mp4
```

**Web UI:**
```bash
cd "/Users/santipapmay/Youtube automation"
python3 app.py        # opens http://localhost:8000 ; keep the terminal open
```
- `PORT=8080 python3 app.py` to change port. `NO_OPEN=1` to skip auto-opening a browser
  (used during automated testing).
- The page only works while the server is running. Closing the terminal / Ctrl+C stops it.
- A background instance may have been left running on :8000 from a prior session — it is
  **ephemeral**, don't rely on it. Check with `lsof -nP -iTCP:8000 -sTCP:LISTEN`.

## Encode settings (the contract — keep CLI & web identical via `build_ffmpeg_cmd`)

- Container/codec: MP4, H.264 (`libx264`), `-preset fast`, `-crf 18`, `-pix_fmt yuv420p`.
- Resolution/fps: scaled to `1920x1080` (`scale=1920:1080,setsar=1`), `-r 30`.
- Audio: AAC `-b:a 192k`. `-movflags +faststart`.
- Looping/trim: `entries = ceil(audio_duration / 13)` image slots cycled over the set, then
  `-t <audio_duration>` trims to the exact audio length (no trailing silence).
- Concat demuxer: temp `_concat.txt` with **absolute** paths, single-quotes escaped as
  `'\''`, `-safe 0`, and the final `file` line repeated without a `duration` (last-frame
  quirk safety). The temp file is deleted in a `finally` block.
- Motion: `zoompan` (Ken Burns) with per-image-reset zoom **and** alternating pan; images
  pre-scaled to `2560x1440` for smoothness. `--zoom none` falls back to plain `scale`.
- Polish (default on): video `fade` in/out + audio `afade` in/out (`--fade SECONDS`, 0=off)
  and `loudnorm=I=-14:TP=-1.5:LRA=11` (`--no-normalize` to skip). Built by `_video_filter`
  / `_audio_filter`; web sends `zoom`, `fade`, `normalize` form fields to `POST /render`.

**Roadmap (requested enhancements):** ✅ zoom, ✅ pan, ✅ fades, ✅ loudness-normalize.
⏳ Next: background music bed (extra audio input + `amix`/sidechain duck). ⏳ Then: crossfade
transitions between images (needs a per-image-clip pipeline — `xfade` can't run on the
single concat-demuxer stream).

## Key design decisions (and why)

1. **Stdlib only / no pip** — matches the user's zero-setup goal; `requirements.txt` empty.
2. **`from __future__ import annotations`** in both `.py` files — lets modern hints
   (`list[Path]`, `int | None`) run on the user's **Python 3.9.6**. Do not remove without
   confirming the target Python.
3. **Natural/numeric image sort** — user names images `1, 2, 3 …`; the user explicitly
   asked for numeric order (1,2,3,10,11), not alphabetical (1,10,2).
4. **ffmpeg concat demuxer** (not moviepy) — per original spec; lightweight, no deps.
5. **`build_ffmpeg_cmd` extracted** so CLI and web never drift on encode settings.
6. **Web bind to localhost only** — safety; it's a personal tool.
7. **Background render thread + progress polling** — real progress bar for long renders.

## Gotchas (do not regress)

- **ffmpeg stderr must NOT go to a PIPE that isn't drained** (web path, `_render_job`).
  A bug (fixed 2026-05-29) sent stderr to a PIPE while only stdout (`-progress pipe:1`)
  was read; on long renders ffmpeg's stderr filled the ~64KB pipe buffer, blocked, and
  the render **froze at 0% CPU mid-way** (showed ~67% in the UI). Short clips didn't
  write enough stderr to trip it. Fix: drop `-stats`, add `-nostats`, and send stderr to
  a **file** (`job_dir/ffmpeg.log`), not a pipe. Keep it that way. The CLI path
  (`create_video` → `subprocess.run(capture_output=True)`) is safe because `run()` drains
  both pipes; only the manual `Popen` web path needed care.

- **File pickers must not be wrapped in `<label>` while also using a JS
  `onclick → input.click()`** (`index.html`). That fired the file dialog twice and made
  selection unreliable ("I selected files but it asked again"). Fixed 2026-05-29: drop
  zones are plain `<div>`s with a single click handler + drag-and-drop, a `.has-file`
  (green) state so a registered selection is visible, and a `pageshow` re-sync so the
  displayed text always matches the input's real `.files` after a reload. Index is served
  with `Cache-Control: no-store` so UI edits always load fresh.

- **`zoompan` drifts unless reset per image** (`_zoom_filter`). Fed by the concat demuxer,
  a normal `zoompan` keeps accumulating zoom across all images instead of restarting each
  one. The fix drives the zoom expression by `mod(on, frames_per_image)` (and image index
  via `floor(on/frames_per_image)` for alternate). Verified empirically with a white-border
  test image (top-row luminance resets HIGH→low→HIGH at each image boundary). Images are
  pre-scaled to `2560x1440` before zoompan so the slow zoom stays smooth (no jitter). Keep
  the `mod()` reset — don't "simplify" to a plain accumulating `zoom+0.001`.

- **Download must survive server restarts** (`app.py`). Originally `JOBS` was in-memory
  only, so restarting the server orphaned the result and `/file/<id>` 404'd — the browser
  then saved the HTML error page as a `.html` file. Fixed 2026-05-29 by persisting each
  render to `renders/<job_id>/` (`output.mp4` + `job.json`) and loading jobs from disk on
  startup. Don't revert to memory-only job state.

## Verification already performed (all passed)

CLI:
- No args → usage + exit 1. Bad audio path / empty image folder → clear error + exit 1.
- Rendered a 30s sine + 3 colored 1080p PNGs (`1/2/3.png`): output was H.264 1920×1080
  `yuv420p` + AAC, duration **exactly 30.0s** (trimmed from 39s of slots); `_concat.txt`
  removed. `_natural_key` sorts `[10,2,1,11,3]` → `[1,2,3,10,11]`.

Web (driven with curl):
- `GET /` → 200 + UI. `POST /render` (1 audio + 3 images) → job_id.
- `/progress` climbed 0 → 100% with correct summary `{duration 30.0, images 3, slots 3,
  loops 1.0, size 0.4MB}`. Download had `Content-Disposition: attachment; filename="a.mp4"`
  and the MP4 verified as 30.0s H.264 1080p + AAC.
- Error paths: no images → 400 JSON; unknown job → 404 JSON. Server start/stop clean.

Quick re-verify recipe (fixtures):
```bash
mkdir -p /tmp/mv/img
ffmpeg -y -f lavfi -i sine=frequency=440:duration=30 -c:a aac /tmp/mv/a.m4a
for i in 1 2 3; do ffmpeg -y -f lavfi -i color=c=gray:s=1920x1080:d=1 -frames:v 1 /tmp/mv/img/$i.png; done
python3 make_video.py /tmp/mv/a.m4a /tmp/mv/img /tmp/mv/out.mp4
ffprobe -v error -show_entries format=duration -of json /tmp/mv/out.mp4   # expect ~30.0
```

## Known limitations / possible next steps

- **`cgi` deprecation**: `app.py` uses stdlib `cgi`, removed in **Python 3.13**. Works on
  the user's 3.9.6. If Python is upgraded past 3.12, replace the multipart parsing
  (e.g. with the `email` module or a small hand-rolled parser). The CLI is unaffected.
- **`renders/` grows over time**: outputs are kept as history (uploaded inputs are deleted
  after each render; only `output.mp4` + `job.json` remain). No automatic pruning — delete
  old `renders/<job_id>/` dirs (or the whole folder) to reclaim space.
- **Images must be 16:9** — anything else is stretched to 1920×1080 (no letterboxing).
- **Fixed parameters** — 13s/image, 30fps are constants. Could be exposed as CLI flags /
  web form fields if the user wants control.
- **No automated test file** — verification has been manual (recipe above).

## User preferences observed

- Wants things **verified**, not just claimed done.
- Prefers a clean, working web UI alongside the CLI.
- Communication: this assistant was running OMC "caveman" terse mode in prose — short,
  direct, no filler. Match that.
