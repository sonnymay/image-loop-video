# make_video

Combine an audio file and a folder of 16:9 images into a single looping MP4 — locally, no upload.

Each image shows for 13 seconds with a slow **Ken Burns zoom**, the images loop in numeric
filename order (`1, 2, 3 …`), and the video is trimmed to exactly the length of the audio
(no trailing silence).

## Prerequisites

- **FFmpeg** (provides `ffmpeg` + `ffprobe`):
  ```bash
  brew install ffmpeg
  ```
- **Python 3.9+** — already on macOS. No `pip install` needed; the script uses only the
  standard library.

## Usage

```bash
python3 make_video.py <audio_file> <images_folder> <output_file> [--res HEIGHT] [--zoom MODE] [--fade SECONDS] [--no-normalize]
```

Example:

```bash
python3 make_video.py audio.mp3 ./images ./output/story.mp4 --res 480 --zoom alternate
```

Options:

| Flag | Effect |
|------|--------|
| `--res HEIGHT` | Output resolution: `480` (default), `720`, `1080`, `360`. **Lower = much faster + smaller.** |
| `--zoom MODE` | Ken Burns effect: `alternate` (default), `in`, `out`, `inout`, `none` |
| `--fade SECONDS` | Fade in/out (video + audio); `0` disables (default `1`) |
| `--no-normalize` | Skip loudness normalization (default: normalize to −14 LUFS) |

**Speed tip:** resolution is by far the biggest factor in render time — 1080p is roughly
**12× slower** than the 480p default. Storytelling over static images looks fine at 480p, so
that's the default; bump to 720p/1080p only if you need it sharper.

`--zoom` modes: `alternate` = image 1 zooms in, image 2 out, …; `in`/`out` = every image
the same way; `inout` = zoom in then back out; `none` = static. All zooming modes also add a
gentle alternating pan. The output folder is created automatically if it doesn't exist.

## Web interface

Prefer clicking to typing? Run the local web app:

```bash
python3 app.py
```

It opens `http://localhost:8000` in your browser. Choose an audio file and your
images, click **Create video**, watch the progress bar, then preview and download
the MP4. To use a different port: `PORT=8080 python3 app.py`.

- Same engine as the CLI (identical encode settings) — it just adds a UI and a
  live progress bar.
- Binds to `localhost` only, so it is not exposed to your network.
- Stdlib only (no `pip install`). Needs Python ≤ 3.12 because it uses the
  standard-library `cgi` module for uploads; the CLI works on any Python 3.9+.
- Rendered videos are saved under `renders/<id>/output.mp4`, so **download links keep
  working even if you restart the server**. This folder is your render history — delete
  old subfolders (or the whole `renders/` folder) anytime to free space.

## Notes

- **Images must be 16:9.** They are scaled to 1920×1080 (no padding/letterboxing); any
  other aspect ratio will be stretched.
- **Image order:** numeric by filename (`1.png, 2.png … 10.png`), then they loop until the
  audio ends.
- **Supported image formats:** `.jpg`, `.jpeg`, `.png`, `.webp`.
- **Supported audio formats:** `.mp3`, `.wav`, `.m4a` (anything ffprobe can read).
- **Output:** 1920×1080 H.264 (CRF 18, preset `fast`, yuv420p) MP4 with AAC 192k audio and
  `+faststart`, trimmed to the audio duration.
- A temporary `_concat.txt` is written next to the output and deleted automatically.
