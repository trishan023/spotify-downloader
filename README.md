# Spotify Downloader

A Flask web app to download Spotify tracks, albums, and playlists as MP3s using [spotDL v4](https://github.com/spotDL/spotify-downloader).

No Spotify API credentials required — spotDL uses Spotify's public web API, finds the best match on YouTube, and converts to MP3 automatically.

![Python](https://img.shields.io/badge/python-3.10%2B-blue) ![Flask](https://img.shields.io/badge/flask-3.x-lightgrey) ![spotDL](https://img.shields.io/badge/spotDL-4.x-1DB954)

---

## Features

- Paste any Spotify URL (track, album, or playlist)
- Choose your output folder
- Real-time download progress via Server-Sent Events
- Download history for the current session
- Clean dark UI with Spotify green accents

## Requirements

- Python 3.10+
- [Poetry](https://python-poetry.org/)
- [ffmpeg](https://ffmpeg.org/) (for audio conversion)

## Setup

```bash
# Install ffmpeg (macOS)
brew install ffmpeg

# Clone and install dependencies
git clone https://github.com/trishan023/spotify-downloader.git
cd spotify-downloader
poetry install

# Start the server
poetry run python app.py
```

Open http://localhost:5000 in your browser.

## Usage

1. Paste a Spotify URL into the input field
2. Set your output folder (defaults to `~/Music`)
3. Click **Download**
4. Watch the progress bar — your MP3(s) will appear in the output folder when done

## Project Structure

```
spotify-downloader/
├── app.py              # Flask app + API routes
├── templates/
│   └── index.html      # Single-page UI
├── static/
│   ├── style.css       # Dark theme styles
│   └── script.js       # SSE progress handling
└── pyproject.toml      # Poetry config
```

## Tech Stack

- **Flask** — web server
- **spotDL v4** — download engine (wraps yt-dlp + ffmpeg)
- **Vanilla JS** — no framework, SSE for real-time progress
