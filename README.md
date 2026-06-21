# 🎬 image-loop-video

![Python](https://img.shields.io/badge/Python-3776AB?style=flat&logo=python&logoColor=white)
![FFmpeg](https://img.shields.io/badge/FFmpeg-007808?style=flat&logo=ffmpeg&logoColor=white)
![CLI](https://img.shields.io/badge/CLI-Tool-black?style=flat)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)

Combine an audio file and a folder of images into a single looping MP4 with Ken Burns zoom — fully local, no upload required. Ships as both a **CLI tool** and a **local web UI**.

Built with Python standard library + FFmpeg. No cloud, no subscription, no nonsense.

---

## ✨ Features

- 🖼️ **Ken Burns zoom** — smooth zoom-in/out effect on each image
- 🎵 **Audio loop** — audio file loops to match total video length
- 📐 **Selectable output resolution** — defaults to 1080p, configurable
- 🖥️ **Local web UI** — drag-and-drop interface via browser
- ⌨️ **CLI mode** — scriptable, automation-friendly
- 📦 **Zero external Python deps** — uses stdlib + FFmpeg only

---

## 🛠 Tech Stack

| Tool | Purpose |
|---|---|
| Python (stdlib) | Core scripting, file handling, server |
| FFmpeg | Video rendering, Ken Burns zoom, audio mixing |
| HTML/JS | Local web UI (served by Python http.server) |

---

## 💼 What This Code Shows

- **FFmpeg** command construction and subprocess management in Python
- **Ken Burns effect** implementation via FFmpeg `zoompan` filter
- Building a usable **local web UI** with zero external dependencies
- **CLI + GUI dual-mode** tool architecture in a single codebase
- Clean, practical Python scripting for media processing

---

## 🚀 Run Locally

**Requirements:** Python 3.8+ and FFmpeg installed

```bash
git clone https://github.com/sonnymay/image-loop-video
cd image-loop-video
pip install -r requirements.txt
```

**Web UI:**
```bash
python app.py
# Open http://localhost:5000
```

**CLI:**
```bash
python make_video.py --images ./images --audio track.mp3 --output output.mp4
```

---

## 📄 License

MIT
