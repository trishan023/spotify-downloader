# Spotify Downloader

A Flask web app to download Spotify tracks, albums, and playlists as MP3s using [spotDL v4](https://github.com/spotDL/spotify-downloader).

No Spotify API credentials required — spotDL uses Spotify's public web API, finds the best match on YouTube, and converts to MP3 automatically.

![Python](https://img.shields.io/badge/python-3.10%2B-blue) ![Flask](https://img.shields.io/badge/flask-3.x-lightgrey) ![spotDL](https://img.shields.io/badge/spotDL-4.x-1DB954)

---

## Features

- Paste any Spotify URL (track, album, or playlist)
- Connect your Spotify account to download your **Liked Songs** library
- Choose your output folder with a native Finder picker
- Real-time download progress via Server-Sent Events
- Per-track status queue with live indicators
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

---

## UI Walkthrough

### 1 — Default view

When you first open the app you see the main card. Paste any Spotify link and hit **Download** straight away — no account connection needed.

```
┌──────────────────────────────────────────────┐
│                                              │
│                    ●  ●  ●                   │  ← Spotify logo (green)
│             Spotify Downloader               │
│       Paste a Spotify link and download      │
│                  as MP3                      │
│                                              │
│  ┌──────────────────────────────────────────┐│
│  │  ♫  Connect Spotify Account              ││  ← green-tinted outline button
│  └──────────────────────────────────────────┘│
│                                              │
│  Spotify URL                                 │
│  ┌──────────────────────────────────────────┐│
│  │  https://open.spotify.com/track/...      ││
│  └──────────────────────────────────────────┘│
│                                              │
│  Output folder                               │
│  ┌─────────────────────────────┐  ┌────────┐│
│  │  ~/Music                    │  │ Browse ││
│  └─────────────────────────────┘  └────────┘│
│                                              │
│  ┌──────────────────────────────────────────┐│
│  │              Download                    ││  ← Spotify green button
│  └──────────────────────────────────────────┘│
│                                              │
└──────────────────────────────────────────────┘
```

**Steps:**

1. Paste a Spotify track, album, artist, or playlist URL into the **Spotify URL** field.
2. The **Output folder** defaults to `~/Music`. Click **Browse** to pick a different folder with the native macOS Finder dialog.
3. Click **Download**.

---

### 2 — Spotify account connected

Click **Connect Spotify Account** to authorise the app via OAuth. Once connected the banner switches to show your account name and a **Download Liked Songs** shortcut.

```
┌──────────────────────────────────────────────┐
│                                              │
│                    ●  ●  ●                   │
│             Spotify Downloader               │
│       Paste a Spotify link and download      │
│                  as MP3                      │
│                                              │
│  ┌──────────────────────────────────────────┐│
│  │  ● Your Name                  Disconnect ││  ← green dot, ghost link
│  └──────────────────────────────────────────┘│
│  ┌──────────────────────────────────────────┐│
│  │  ♥  Download Liked Songs                 ││  ← solid green button
│  └──────────────────────────────────────────┘│
│  ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─  │
│                                              │
│  Spotify URL                                 │
│  ┌──────────────────────────────────────────┐│
│  │  https://open.spotify.com/track/...      ││
│  └──────────────────────────────────────────┘│
│  ...                                         │
└──────────────────────────────────────────────┘
```

- The **green dot** confirms the OAuth session is active.
- **Download Liked Songs** kicks off a full library download in one click — no URL needed.
- Click **Disconnect** to revoke the session and return to the default banner.

---

### 3 — Download in progress

Once a download starts, the form locks and a progress section appears below it. A **Cancel** button replaces the download button's text area.

```
┌──────────────────────────────────────────────┐
│  ...form (disabled during download)...       │
│                                              │
│  ┌───────────────────────┐  ┌─────────────┐ │
│  │       Download        │  │  ✕  Cancel  │ │  ← Cancel is red-outlined
│  └───────────────────────┘  └─────────────┘ │
│                                              │
│  Downloading track 3 of 12        3 / 12     │
│  ████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  │  ← green progress bar
│                                              │
│  ┌─────────────────────────────────────────┐ │
│  │  PLAYLIST  Chill Mix  →  ~/Music/Chill  │ │  ← collection info strip
│  └─────────────────────────────────────────┘ │
│                                              │
│  ●  Blinding Lights                          │  ● green pulsing = active
│     The Weeknd                               │
│  ●  Levitating                               │  ● solid green = done
│     Dua Lipa                                 │
│  ●  Stay                                     │  ● solid green = done
│     Justin Bieber                            │
│  ○  Peaches                                  │  ○ dark = pending
│     Justin Bieber                            │
│  ○  good 4 u                                 │
│     Olivia Rodrigo                           │
│                                              │
└──────────────────────────────────────────────┘
```

**Track dot legend:**

| Dot colour | Meaning |
|-----------|---------|
| Dark grey | Pending — not yet started |
| Green pulsing | Actively downloading / converting |
| Solid green | Done |
| Red | Failed (hover to see error) |

The **collection info strip** shows the type badge (TRACK / ALBUM / PLAYLIST / ARTIST), the collection name, and the destination folder.

---

### 4 — Outcome banners

When a download finishes one of three banners slides in:

```
  ✔  12 tracks downloaded successfully         ← green  (all done)

  ✘  Download failed — spotDL error            ← red    (error)

  ⚠  Download cancelled                        ← amber  (user cancelled)
```

After a success or failure you can paste a new URL and start again immediately.

---

### 5 — Download history

Every completed job (success, error, or cancelled) is appended to a **Download History** list at the bottom of the card for the current session.

```
  DOWNLOAD HISTORY
  ┌───────────────────────────────────────────┐
  │  DONE       ~/Music/Chill Mix             │
  │  DONE       ~/Music/Blinding Lights.mp3   │
  │  CANCELLED  ~/Music/Top 50                │
  │  ERROR      ~/Music/My Playlist           │
  └───────────────────────────────────────────┘
```

Badges are colour-coded: **green** = done, **red** = error, **amber** = cancelled.

---

## Project Structure

```
spotify-downloader/
├── app.py              # Flask app + API routes + SSE streaming
├── templates/
│   └── index.html      # Single-page UI
├── static/
│   ├── style.css       # Dark theme, Spotify green accents
│   └── script.js       # SSE progress handling, OAuth flow
└── pyproject.toml      # Poetry config
```

## Tech Stack

- **Flask** — web server
- **spotDL v4** — download engine (wraps yt-dlp + ffmpeg)
- **Vanilla JS** — no framework, SSE for real-time progress
- **Spotify OAuth** — optional account connection for Liked Songs
