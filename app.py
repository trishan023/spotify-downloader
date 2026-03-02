import json
import logging
import os
import queue
import re
import secrets
import threading
import time
import urllib.parse
import uuid
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, redirect, render_template, request, session
from spotdl import Downloader, Spotdl
from spotdl.utils.config import SPOTIFY_OPTIONS

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("spotify-dl")

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", secrets.token_hex(32))

# ── Spotify OAuth constants ────────────────────────────────────────────────────
SPOTIFY_AUTH_URL     = "https://accounts.spotify.com/authorize"
SPOTIFY_TOKEN_URL    = "https://accounts.spotify.com/api/token"
SPOTIFY_API_BASE     = "https://api.spotify.com/v1"
SPOTIFY_REDIRECT_URI = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8080/auth/callback")
SPOTIFY_SCOPE        = "user-library-read"

# Lazy singleton — initialized on first download request
_spotdl: Spotdl | None = None
_spotdl_lock = threading.Lock()


def get_spotdl() -> Spotdl:
    global _spotdl
    if _spotdl is None:
        with _spotdl_lock:
            if _spotdl is None:
                log.info("Initializing Spotify client...")
                client_id = os.getenv("SPOTIFY_CLIENT_ID", SPOTIFY_OPTIONS["client_id"])
                client_secret = os.getenv("SPOTIFY_CLIENT_SECRET", SPOTIFY_OPTIONS["client_secret"])
                log.info("Using client_id: %s...", client_id[:8])
                _spotdl = Spotdl(
                    client_id=client_id,
                    client_secret=client_secret,
                    downloader_settings={"threads": 1, "simple_tui": True},
                )
                log.info("Spotify client ready.")
    return _spotdl

# ── Spotify OAuth helpers ──────────────────────────────────────────────────────

def _refresh_access_token(refresh_token: str) -> dict | None:
    """Exchange a refresh token for a new access token. Returns token dict or None."""
    resp = requests.post(
        SPOTIFY_TOKEN_URL,
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        auth=(os.getenv("SPOTIFY_CLIENT_ID"), os.getenv("SPOTIFY_CLIENT_SECRET")),
        timeout=10,
    )
    if resp.status_code == 200:
        return resp.json()
    log.warning("Token refresh failed: %s %s", resp.status_code, resp.text)
    return None


def get_access_token() -> str | None:
    """Return a valid access token from the session, refreshing if near-expiry. None if not connected."""
    if "access_token" not in session:
        return None
    if time.time() > session.get("token_expires_at", 0) - 60:
        refreshed = _refresh_access_token(session["refresh_token"])
        if refreshed is None:
            session.clear()
            return None
        session["access_token"]     = refreshed["access_token"]
        session["token_expires_at"] = time.time() + refreshed["expires_in"]
        if "refresh_token" in refreshed:
            session["refresh_token"] = refreshed["refresh_token"]
    return session["access_token"]


def fetch_liked_song_urls(access_token: str) -> list[str]:
    """Paginate GET /v1/me/tracks and return a list of Spotify track URLs."""
    urls     = []
    next_url = f"{SPOTIFY_API_BASE}/me/tracks?limit=50"
    headers  = {"Authorization": f"Bearer {access_token}"}
    while next_url:
        resp = requests.get(next_url, headers=headers, timeout=15)
        resp.raise_for_status()
        body = resp.json()
        for item in body.get("items", []):
            track = item.get("track")
            if track and track.get("id"):
                urls.append(f"https://open.spotify.com/track/{track['id']}")
        next_url = body.get("next")
    return urls


SPOTIFY_URL_RE = re.compile(
    r'^https://open\.spotify\.com/(track|album|playlist|artist)/[A-Za-z0-9]{22}(\?.*)?$'
)


def is_valid_spotify_url(url: str) -> bool:
    """Return True if url matches the expected Spotify URL format."""
    return bool(SPOTIFY_URL_RE.match(url))


def detect_url_type(url: str) -> str:
    """Returns 'album', 'playlist', 'artist', 'track', or 'unknown'."""
    if re.search(r'open\.spotify\.com/album/', url):
        return 'album'
    if re.search(r'open\.spotify\.com/playlist/', url):
        return 'playlist'
    if re.search(r'open\.spotify\.com/artist/', url):
        return 'artist'
    if re.search(r'open\.spotify\.com/track/', url):
        return 'track'
    return 'unknown'


def validate_output_dir(raw_path: str) -> str | None:
    """Resolve *raw_path* and return the absolute string if it lives under the
    user's home directory.  Returns None when the path escapes home."""
    resolved = Path(raw_path).expanduser().resolve()
    home = Path.home().resolve()
    if resolved == home or str(resolved).startswith(str(home) + os.sep):
        return str(resolved)
    return None


def sanitize_folder_name(name: str) -> str:
    """Strip characters not allowed in folder names on macOS/Linux/Windows."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', name)
    name = name.strip('. ')
    return name or 'Download'


MAX_CONCURRENT_JOBS = 1
JOB_TTL_SECONDS = 600  # clean up finished jobs after 10 minutes

# In-memory job store: job_id -> {"status": str, "progress": int, "message": str, "output": str}
jobs: dict[str, dict] = {}
# Per-job event queues for SSE
job_queues: dict[str, queue.Queue] = {}
# Per-job cancel signals
cancel_events: dict[str, threading.Event] = {}
# Track when jobs finish for TTL cleanup
job_finished_at: dict[str, float] = {}
_jobs_lock = threading.Lock()


def _active_job_count() -> int:
    """Return number of jobs that are still running (not done/error/cancelled)."""
    return sum(1 for j in jobs.values() if j.get("status") in ("queued", "starting", "searching", "downloading"))


def _cleanup_stale_jobs() -> None:
    """Remove finished jobs older than JOB_TTL_SECONDS."""
    now = time.time()
    stale = [jid for jid, t in job_finished_at.items() if now - t > JOB_TTL_SECONDS]
    for jid in stale:
        jobs.pop(jid, None)
        job_queues.pop(jid, None)
        cancel_events.pop(jid, None)
        job_finished_at.pop(jid, None)


def run_download(job_id: str, url: str, output_dir: str) -> None:
    """Run spotDL download in a background thread and push progress via queue."""
    q = job_queues[job_id]
    cancel = cancel_events[job_id]

    def push(event: str, data: dict) -> None:
        q.put({"event": event, "data": data})
        jobs[job_id].update(data)

    def pushlog(msg: str, level: str = "info") -> None:
        log.info("[%s] %s", job_id[:8], msg)
        q.put({"event": "log", "data": {"level": level, "msg": msg}})

    try:
        log.info("[%s] Job started — URL: %s", job_id[:8], url)
        pushlog("Job started")
        push("progress", {"status": "starting", "progress": 5, "message": "Initializing spotDL..."})

        Path(output_dir).mkdir(parents=True, exist_ok=True)

        pushlog("Initializing Spotify client...")
        spotdl = get_spotdl()
        pushlog("Spotify client ready")

        pushlog(f"Searching Spotify for: {url}")
        push("progress", {"status": "searching", "progress": 15, "message": "Searching for tracks..."})

        songs = spotdl.search([url])

        if cancel.is_set():
            push("cancelled", {"status": "cancelled", "progress": 0, "message": "Cancelled", "downloaded": 0, "total": 0})
            return

        if not songs:
            pushlog("No tracks found for this URL", "warn")
            push("error", {"status": "error", "progress": 0, "message": "No tracks found for the given URL."})
            q.put(None)  # sentinel
            return

        total = len(songs)
        names = ", ".join(s.name for s in songs)
        pushlog(f"Found {total} track(s): {names}")

        # Auto-create a named subfolder for albums, playlists, and artists
        url_type = detect_url_type(url)
        collection_name: str | None = None
        if url_type in ('album', 'playlist', 'artist') and songs:
            s0 = songs[0]
            if url_type == 'album':
                collection_name = (
                    getattr(s0, 'album_name', None)
                    or getattr(s0, 'list_name', None)
                )
            else:
                collection_name = (
                    getattr(s0, 'list_name', None)
                    or getattr(s0, 'album_name', None)
                )
            if collection_name:
                safe_name = sanitize_folder_name(collection_name)
                output_dir = str(Path(output_dir) / safe_name)
                Path(output_dir).mkdir(parents=True, exist_ok=True)
                pushlog(f"Auto-created folder: {output_dir}")

        track_list = [
            {"name": s.name, "artist": getattr(s, "artist", "") or ""}
            for s in songs
        ]
        push("tracks", {
            "status": "downloading",
            "progress": 25,
            "message": f"Found {total} track(s). Starting download...",
            "tracks": track_list,
            "collection_type": url_type if url_type in ('album', 'playlist', 'artist') else None,
            "collection_name": collection_name,
            "output": output_dir,
        })

        pushlog(f"Downloading to {output_dir}...")
        downloader = Downloader({"output": output_dir, "simple_tui": True, "threads": 1})

        downloaded = []
        failed = []

        for i, song in enumerate(songs):
            if cancel.is_set():
                n_done = len(downloaded)
                push("cancelled", {
                    "status": "cancelled",
                    "progress": round(25 + 75 * i / total),
                    "message": f"Cancelled · {n_done} of {total} downloaded",
                    "downloaded": n_done,
                    "total": total,
                })
                return

            push("track_start", {
                "index": i,
                "name": song.name,
                "progress": round(25 + 75 * i / total),
            })
            try:
                results = downloader.download_multiple_songs([song])
                song_obj, path = results[0]
                if path is not None:
                    downloaded.append((song_obj, path))
                    push("track_done", {
                        "index": i,
                        "name": song.name,
                        "success": True,
                        "progress": round(25 + 75 * (i + 1) / total),
                    })
                else:
                    failed.append((song_obj, None))
                    push("track_done", {
                        "index": i,
                        "name": song.name,
                        "success": False,
                        "reason": "No audio source found",
                        "progress": round(25 + 75 * (i + 1) / total),
                    })
            except Exception as exc:
                failed.append((song, None))
                reason = str(exc).splitlines()[0][:120]
                pushlog(f"Failed to download '{song.name}': {exc}", "error")
                push("track_done", {
                    "index": i,
                    "name": song.name,
                    "success": False,
                    "reason": reason or "Unknown error",
                    "progress": round(25 + 75 * (i + 1) / total),
                })

        pushlog(f"Done — {len(downloaded)} downloaded, {len(failed)} failed",
                "done" if downloaded else "warn")

        if downloaded:
            push("done", {
                "status": "done",
                "progress": 100,
                "message": f"Downloaded {len(downloaded)} of {total} track(s)",
                "output": output_dir,
            })
        else:
            push("error", {
                "status": "error",
                "progress": 0,
                "message": f"Download failed. {len(failed)} track(s) could not be downloaded.",
            })

    except Exception as exc:
        log.exception("[%s] Unhandled error: %s", job_id[:8], exc)
        safe_msg = "An unexpected error occurred. Check the server logs for details."
        jobs[job_id].update({"status": "error", "progress": 0, "message": safe_msg})
        job_queues[job_id].put({"event": "log",   "data": {"level": "error", "msg": safe_msg}})
        job_queues[job_id].put({"event": "error", "data": {"status": "error", "message": safe_msg}})

    finally:
        job_finished_at[job_id] = time.time()
        q.put(None)  # sentinel — tells the SSE generator to close


def run_liked_songs_download(job_id: str, track_urls: list[str], output_dir: str) -> None:
    """Download a pre-fetched list of Spotify track URLs, emitting the same SSE events as run_download."""
    q = job_queues[job_id]

    def push(event: str, data: dict) -> None:
        q.put({"event": event, "data": data})
        jobs[job_id].update(data)

    def pushlog(msg: str, level: str = "info") -> None:
        log.info("[%s] %s", job_id[:8], msg)
        q.put({"event": "log", "data": {"level": level, "msg": msg}})

    try:
        push("progress", {"status": "starting", "progress": 5,
                           "message": f"Preparing {len(track_urls)} liked song(s)..."})

        Path(output_dir).mkdir(parents=True, exist_ok=True)
        liked_dir = str(Path(output_dir) / "Liked Songs")
        Path(liked_dir).mkdir(parents=True, exist_ok=True)
        pushlog(f"Output folder: {liked_dir}")

        push("progress", {"status": "searching", "progress": 10,
                           "message": f"Resolving {len(track_urls)} track(s) via spotDL..."})

        spotdl = get_spotdl()
        songs = spotdl.search(track_urls)

        if not songs:
            push("error", {"status": "error", "progress": 0,
                            "message": "No tracks could be resolved."})
            q.put(None)
            return

        total      = len(songs)
        track_list = [{"name": s.name, "artist": getattr(s, "artist", "") or ""} for s in songs]
        push("tracks", {
            "status":          "downloading",
            "progress":        25,
            "message":         f"Found {total} liked track(s). Starting download...",
            "tracks":          track_list,
            "collection_type": "playlist",
            "collection_name": "Liked Songs",
            "output":          liked_dir,
        })

        downloader = Downloader({"output": liked_dir, "simple_tui": True, "threads": 1})
        downloaded = []
        failed     = []

        for i, song in enumerate(songs):
            push("track_start", {
                "index":    i,
                "name":     song.name,
                "progress": round(25 + 75 * i / total),
            })
            try:
                results      = downloader.download_multiple_songs([song])
                song_obj, path = results[0]
                if path is not None:
                    downloaded.append((song_obj, path))
                    push("track_done", {"index": i, "name": song.name, "success": True,
                                         "progress": round(25 + 75 * (i + 1) / total)})
                else:
                    failed.append((song_obj, None))
                    push("track_done", {"index": i, "name": song.name, "success": False,
                                         "reason": "No audio source found",
                                         "progress": round(25 + 75 * (i + 1) / total)})
            except Exception as exc:
                failed.append((song, None))
                pushlog(f"Failed '{song.name}': {exc}", "error")
                push("track_done", {"index": i, "name": song.name, "success": False,
                                     "reason": str(exc).splitlines()[0][:120],
                                     "progress": round(25 + 75 * (i + 1) / total)})

        if downloaded:
            push("done", {
                "status":   "done",
                "progress": 100,
                "message":  f"Downloaded {len(downloaded)} of {total} liked track(s)",
                "output":   liked_dir,
            })
        else:
            push("error", {
                "status":  "error",
                "progress": 0,
                "message": f"Download failed. {len(failed)} track(s) could not be downloaded.",
            })

    except Exception as exc:
        log.exception("[%s] Unhandled error: %s", job_id[:8], exc)
        safe_msg = "An unexpected error occurred. Check the server logs for details."
        jobs[job_id].update({"status": "error", "progress": 0, "message": safe_msg})
        job_queues[job_id].put({"event": "error", "data": {"status": "error", "message": safe_msg}})
    finally:
        job_finished_at[job_id] = time.time()
        q.put(None)


@app.route("/pick-folder", methods=["POST"])
def pick_folder():
    """Open a native macOS folder picker and return the selected path."""
    import subprocess
    result = subprocess.run(
        ["osascript", "-e", 'POSIX path of (choose folder with prompt "Select download folder:")'],
        capture_output=True, text=True,
    )
    path = result.stdout.strip()
    if not path:
        return jsonify({"error": "No folder selected"}), 204
    return jsonify({"path": path.rstrip("/")})


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/download", methods=["POST"])
def download():
    body = request.get_json(force=True)
    url = (body.get("url") or "").strip()
    output = (body.get("output") or "~/Music").strip()
    output_dir = validate_output_dir(output)

    if not url:
        return jsonify({"error": "url is required"}), 400
    if not is_valid_spotify_url(url):
        return jsonify({"error": "Invalid Spotify URL. Accepted formats: track, album, playlist, or artist links."}), 400
    if output_dir is None:
        return jsonify({"error": "Output folder must be inside your home directory."}), 400

    with _jobs_lock:
        _cleanup_stale_jobs()
        if _active_job_count() >= MAX_CONCURRENT_JOBS:
            return jsonify({"error": "A download is already in progress. Please wait for it to finish."}), 429

        job_id = str(uuid.uuid4())
        jobs[job_id] = {"status": "queued", "progress": 0, "message": "Queued", "output": output_dir}
        job_queues[job_id] = queue.Queue()
        cancel_events[job_id] = threading.Event()

    thread = threading.Thread(target=run_download, args=(job_id, url, output_dir), daemon=True)
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/progress/<job_id>")
def progress(job_id: str):
    """Server-Sent Events stream for real-time progress."""
    if job_id not in jobs:
        return jsonify({"error": "job not found"}), 404

    def generate():
        q = job_queues[job_id]
        while True:
            item = q.get()
            if item is None:
                # Send final state then close
                final = jobs.get(job_id, {})
                yield f"event: {final.get('status', 'done')}\ndata: {json.dumps(final)}\n\n"
                break
            yield f"event: {item['event']}\ndata: {json.dumps(item['data'])}\n\n"

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/cancel/<job_id>", methods=["POST"])
def cancel(job_id: str):
    """Signal a running job to stop after the current song."""
    if job_id not in cancel_events:
        return jsonify({"error": "job not found"}), 404
    cancel_events[job_id].set()
    return jsonify({"ok": True})


@app.route("/status/<job_id>")
def status(job_id: str):
    """Polling fallback — returns current job state as JSON."""
    if job_id not in jobs:
        return jsonify({"error": "job not found"}), 404
    return jsonify(jobs[job_id])


# ── Spotify OAuth routes ───────────────────────────────────────────────────────

@app.route("/auth/status")
def auth_status():
    token = get_access_token()
    if token:
        return jsonify({"connected": True, "display_name": session.get("display_name", "")})
    return jsonify({"connected": False})


@app.route("/auth/login")
def auth_login():
    state = secrets.token_hex(16)
    session["oauth_state"] = state
    params = {
        "client_id":     os.getenv("SPOTIFY_CLIENT_ID"),
        "response_type": "code",
        "redirect_uri":  SPOTIFY_REDIRECT_URI,
        "scope":         SPOTIFY_SCOPE,
        "state":         state,
    }
    return redirect(SPOTIFY_AUTH_URL + "?" + urllib.parse.urlencode(params))


@app.route("/auth/callback")
def auth_callback():
    if request.args.get("error"):
        log.warning("OAuth error: %s", request.args.get("error"))
        return redirect("/?auth=error")

    code     = request.args.get("code")
    state    = request.args.get("state")
    expected = session.pop("oauth_state", None)

    if not state or state != expected:
        log.warning("OAuth state mismatch — possible CSRF")
        return redirect("/?auth=error")

    resp = requests.post(
        SPOTIFY_TOKEN_URL,
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": SPOTIFY_REDIRECT_URI},
        auth=(os.getenv("SPOTIFY_CLIENT_ID"), os.getenv("SPOTIFY_CLIENT_SECRET")),
        timeout=10,
    )
    if resp.status_code != 200:
        log.error("Token exchange failed: %s", resp.text)
        return redirect("/?auth=error")

    token_data = resp.json()
    session["access_token"]     = token_data["access_token"]
    session["refresh_token"]    = token_data["refresh_token"]
    session["token_expires_at"] = time.time() + token_data["expires_in"]

    me = requests.get(f"{SPOTIFY_API_BASE}/me",
                      headers={"Authorization": f"Bearer {session['access_token']}"}, timeout=10)
    if me.status_code == 200:
        session["display_name"] = me.json().get("display_name", "")

    return redirect("/")


@app.route("/auth/logout", methods=["POST"])
def auth_logout():
    for key in ("access_token", "refresh_token", "token_expires_at", "display_name"):
        session.pop(key, None)
    return jsonify({"ok": True})


@app.route("/download/liked-songs", methods=["POST"])
def download_liked_songs():
    token = get_access_token()
    if not token:
        return jsonify({"error": "Not authenticated with Spotify"}), 401

    body       = request.get_json(force=True)
    output     = (body.get("output") or "~/Music").strip()
    output_dir = validate_output_dir(output)
    if output_dir is None:
        return jsonify({"error": "Output folder must be inside your home directory."}), 400

    try:
        track_urls = fetch_liked_song_urls(token)
    except Exception as exc:
        log.error("Failed to fetch liked songs: %s", exc)
        return jsonify({"error": "Failed to fetch liked songs. Check the server logs for details."}), 500

    if not track_urls:
        return jsonify({"error": "No liked songs found in your Spotify library."}), 400

    with _jobs_lock:
        _cleanup_stale_jobs()
        if _active_job_count() >= MAX_CONCURRENT_JOBS:
            return jsonify({"error": "A download is already in progress. Please wait for it to finish."}), 429

        job_id              = str(uuid.uuid4())
        jobs[job_id]        = {"status": "queued", "progress": 0, "message": "Queued", "output": output_dir}
        job_queues[job_id]  = queue.Queue()
        cancel_events[job_id] = threading.Event()

    threading.Thread(target=run_liked_songs_download,
                     args=(job_id, track_urls, output_dir), daemon=True).start()

    return jsonify({"job_id": job_id})


if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG", "").lower() in ("1", "true"),
            port=int(os.environ.get('PORT', 8080)), threaded=True)
