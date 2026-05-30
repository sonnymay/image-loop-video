#!/usr/bin/env python3
"""Combine an audio file and a folder of 16:9 images into a looping MP4.

Each image is shown for a fixed number of seconds, the images cycle in numeric
filename order, and the final video is trimmed to exactly the audio duration.

Usage:
    python3 make_video.py <audio_file> <images_folder> <output_file>

Uses FFmpeg (ffmpeg + ffprobe) under the hood -- no Python packages required.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

# How long each image is shown, in seconds.
SECONDS_PER_IMAGE = 13

# Output frame rate.
FPS = 30

# Ken Burns zoom: how much each image zooms over its time slot (0.12 = 12%, slow).
ZOOM_AMOUNT = 0.12
# How far to pan across the available margin (0.5 = half of it), alternating per image.
PAN_AMOUNT = 0.5
# Upscale each image before zooming so the slow zoom stays smooth (no jitter).
ZOOM_INTERMEDIATE = "2560:1440"
# Supported zoom modes for the slideshow effect.
ZOOM_MODES = ("alternate", "in", "out", "inout", "none")

# Default fade-in / fade-out duration (seconds) for video and audio.
FADE_SECONDS = 1.0

# Image file extensions we accept (compared lowercased).
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _zoom_filter(mode: str, seconds: int = SECONDS_PER_IMAGE, fps: int = FPS) -> str:
    """Return the ffmpeg `-vf` filter chain for the given Ken Burns zoom mode.

    The zoom is driven by ``mod(on, frames_per_image)`` so it RESETS for every
    image when fed by the concat demuxer (otherwise zoompan drifts continuously).
    ``mode="none"`` returns a plain scale with no zoom.
    """
    p = seconds * fps  # output frames per image
    a = ZOOM_AMOUNT
    progress = f"mod(on,{p})/{p - 1}"  # 0 -> 1 within each image
    if mode == "in":
        z = f"1+{a}*{progress}"
    elif mode == "out":
        z = f"1+{a}*(1-{progress})"
    elif mode == "inout":
        z = f"1+{a}*sin(PI*{progress})"
    elif mode == "alternate":
        # even-indexed images zoom in, odd-indexed zoom out
        z = (f"if(eq(mod(floor(on/{p}),2),0),"
             f"1+{a}*{progress},1+{a}*(1-{progress}))")
    else:  # "none" or unknown -> no zoom
        return "scale=1920:1080,setsar=1"
    # Horizontal pan that alternates direction per image (drifts within the zoom
    # margin, so it stays in-bounds and is zero when zoom == 1).
    dir_x = f"if(eq(mod(floor(on/{p}),2),0),1,-1)"
    x = f"(iw-iw/zoom)/2*(1+{PAN_AMOUNT}*{dir_x}*(2*{progress}-1))"
    y = "ih/2-(ih/zoom/2)"  # vertically centered
    return (
        f"scale={ZOOM_INTERMEDIATE},"
        f"zoompan=z='{z}':d={p}:x='{x}':y='{y}':"
        f"s=1920x1080:fps={fps},setsar=1"
    )


def _clamp_fade(fade: float, duration: float) -> float:
    """Clamp a fade duration so fade-in + fade-out can't exceed the clip."""
    if fade <= 0:
        return 0.0
    return min(fade, duration / 2)


def _video_filter(zoom_mode: str, audio_duration: float, fade: float) -> str:
    """Video `-vf` chain: Ken Burns zoom/pan plus optional fade in/out."""
    vf = _zoom_filter(zoom_mode)
    fade = _clamp_fade(fade, audio_duration)
    if fade > 0:
        out_start = max(0.0, audio_duration - fade)
        vf += f",fade=t=in:st=0:d={fade:g},fade=t=out:st={out_start:g}:d={fade:g}"
    return vf


def _audio_filter(audio_duration: float, fade: float, normalize: bool) -> str:
    """Audio `-af` chain: optional loudness normalize + fade in/out. May be empty."""
    parts = []
    if normalize:
        parts.append("loudnorm=I=-14:TP=-1.5:LRA=11")
    fade = _clamp_fade(fade, audio_duration)
    if fade > 0:
        out_start = max(0.0, audio_duration - fade)
        parts.append(f"afade=t=in:st=0:d={fade:g}")
        parts.append(f"afade=t=out:st={out_start:g}:d={fade:g}")
    return ",".join(parts)


def check_ffmpeg() -> None:
    """Exit with a helpful message if ffmpeg or ffprobe are not installed."""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        print("FFmpeg not found. Install it with: brew install ffmpeg")
        sys.exit(1)


def _natural_key(path: Path) -> list:
    """Sort key that orders numeric filenames the human way (1, 2, ... 10, 11).

    Splits the filename on runs of digits so that numeric chunks compare as
    integers (``2`` before ``10``). Non-numeric names fall back to
    case-insensitive alphabetical ordering.
    """
    parts = re.split(r"(\d+)", path.name.lower())
    return [int(part) if part.isdigit() else part for part in parts]


def get_audio_duration(audio: Path) -> float:
    """Return the duration of an audio file in seconds, via ffprobe JSON output."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(audio),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ffprobe could not read '{audio}':\n{result.stderr.strip()}"
        )

    data = json.loads(result.stdout)
    duration = float(data.get("format", {}).get("duration", 0.0))
    if duration <= 0:
        raise RuntimeError(f"'{audio}' has no readable audio duration.")
    return duration


def get_images(folder: Path) -> list[Path]:
    """Return absolute paths of supported images in *folder*, in numeric order."""
    images = [
        p.resolve()
        for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    ]
    images.sort(key=_natural_key)
    return images


def build_concat_file(
    images: list[Path], audio_duration: float, concat_path: Path
) -> tuple[int, float]:
    """Write an ffmpeg concat-demuxer file covering *audio_duration*.

    Cycles through *images* (looping back to the first after the last) until the
    accumulated runtime exceeds the audio duration. Returns ``(entries, loops)``
    where *entries* is the number of image slots written and *loops* is how many
    times the image set is traversed (may be fractional).
    """
    entries = math.ceil(audio_duration / SECONDS_PER_IMAGE)

    lines: list[str] = []
    last_path = ""
    for i in range(entries):
        image = images[i % len(images)]
        # Escape single quotes for the concat demuxer: ' -> '\''
        escaped = str(image).replace("'", "'\\''")
        last_path = escaped
        lines.append(f"file '{escaped}'")
        lines.append(f"duration {SECONDS_PER_IMAGE}")

    # Repeat the final file once more without a duration so the concat demuxer
    # does not drop the last frame (a well-known quirk of the format).
    if last_path:
        lines.append(f"file '{last_path}'")

    concat_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return entries, entries / len(images)


def build_ffmpeg_cmd(
    concat_path: Path,
    audio: Path,
    output: Path,
    audio_duration: float,
    zoom_mode: str = "alternate",
    fade: float = FADE_SECONDS,
    normalize: bool = True,
) -> list[str]:
    """Return the ffmpeg command (concat demuxer) that renders the trimmed MP4.

    Shared by the CLI (`create_video`) and the web interface so both encode with
    identical settings. ``zoom_mode`` selects the Ken Burns effect (see
    `ZOOM_MODES`); ``fade`` is the fade-in/out seconds (0 disables); ``normalize``
    loudness-normalizes the audio to YouTube's -14 LUFS target.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-stats",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_path),
        "-i",
        str(audio),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-vf",
        _video_filter(zoom_mode, audio_duration, fade),
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(FPS),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
    ]
    audio_filter = _audio_filter(audio_duration, fade, normalize)
    if audio_filter:
        cmd += ["-af", audio_filter]
    cmd += [
        "-t",
        f"{audio_duration:.3f}",
        "-movflags",
        "+faststart",
        str(output),
    ]
    return cmd


def create_video(
    concat_path: Path,
    audio: Path,
    output: Path,
    audio_duration: float,
    zoom_mode: str = "alternate",
    fade: float = FADE_SECONDS,
    normalize: bool = True,
) -> None:
    """Render the MP4 with ffmpeg, trimmed to the audio duration."""
    cmd = build_ffmpeg_cmd(
        concat_path, audio, output, audio_duration, zoom_mode, fade, normalize
    )
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        stderr_tail = "\n".join(result.stderr.strip().splitlines()[-15:])
        raise RuntimeError(f"ffmpeg failed to render the video:\n{stderr_tail}")


def _format_duration(seconds: float) -> str:
    """Format seconds as H:MM:SS."""
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def _format_size(num_bytes: int) -> str:
    """Format a byte count as a human-readable MB string."""
    return f"{num_bytes / (1024 * 1024):.1f} MB"


def _usage() -> None:
    print("Usage: python3 make_video.py <audio_file> <images_folder> <output_file> "
          "[--zoom MODE] [--fade SECONDS] [--no-normalize]")
    print(f"  --zoom MODE      Ken Burns effect: {', '.join(ZOOM_MODES)} "
          "(default: alternate)")
    print("  --fade SECONDS   Fade in/out for video + audio, 0 = off (default: 1)")
    print("  --no-normalize   Skip loudness normalization (default: normalize to -14 LUFS)")
    print("Example: python3 make_video.py audio.mp3 ./images ./output/story.mp4 "
          "--zoom alternate --fade 1.5")


def main() -> int:
    """Parse args, validate inputs, render the video, and print a summary."""
    zoom_mode = "alternate"
    fade = FADE_SECONDS
    normalize = True
    positional = []
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--zoom", "-z"):
            if i + 1 >= len(args):
                _usage()
                return 1
            zoom_mode = args[i + 1]
            i += 2
            continue
        if arg.startswith("--zoom="):
            zoom_mode = arg.split("=", 1)[1]
            i += 1
            continue
        if arg == "--fade":
            if i + 1 >= len(args):
                _usage()
                return 1
            fade = args[i + 1]
            i += 2
            continue
        if arg.startswith("--fade="):
            fade = arg.split("=", 1)[1]
            i += 1
            continue
        if arg in ("--no-normalize", "--no-normalise"):
            normalize = False
            i += 1
            continue
        positional.append(arg)
        i += 1

    if len(positional) != 3:
        _usage()
        return 1
    if zoom_mode not in ZOOM_MODES:
        print(f"Error: unknown zoom mode '{zoom_mode}'. "
              f"Choose from: {', '.join(ZOOM_MODES)}")
        return 1
    try:
        fade = max(0.0, float(fade))
    except (TypeError, ValueError):
        print(f"Error: --fade must be a number of seconds (got '{fade}').")
        return 1

    check_ffmpeg()

    audio = Path(positional[0]).expanduser()
    images_folder = Path(positional[1]).expanduser()
    output = Path(positional[2]).expanduser()

    if not audio.is_file():
        print(f"Error: audio file not found: {audio}")
        return 1

    if not images_folder.is_dir():
        print(f"Error: images folder not found: {images_folder}")
        return 1

    images = get_images(images_folder)
    if not images:
        exts = ", ".join(sorted(IMAGE_EXTS))
        print(f"Error: no images ({exts}) found in: {images_folder}")
        return 1

    try:
        audio_duration = get_audio_duration(audio)
        output.parent.mkdir(parents=True, exist_ok=True)

        concat_path = output.parent / "_concat.txt"
        try:
            entries, loops = build_concat_file(images, audio_duration, concat_path)
            print("Rendering... this may take a minute")
            create_video(
                concat_path, audio, output, audio_duration, zoom_mode, fade, normalize
            )
        finally:
            concat_path.unlink(missing_ok=True)
    except RuntimeError as err:
        print(f"Error: {err}")
        return 1

    print()
    print("Done!")
    print(f"  Audio duration : {_format_duration(audio_duration)} "
          f"({audio_duration:.1f}s)")
    print(f"  Images         : {len(images)}")
    print(f"  Image slots    : {entries} "
          f"({SECONDS_PER_IMAGE}s each, {loops:.1f} loops through the set)")
    print(f"  Zoom effect    : {zoom_mode}")
    print(f"  Fade           : {fade:g}s" if fade > 0 else "  Fade           : off")
    print(f"  Normalize      : {'on (-14 LUFS)' if normalize else 'off'}")
    print(f"  Output size    : {_format_size(output.stat().st_size)}")
    print(f"  Output file    : {output.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
