import json
import logging
import os
import queue
import threading
import uuid
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request
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

# In-memory job store: job_id -> {"status": str, "progress": int, "message": str, "output": str}
jobs: dict[str, dict] = {}
# Per-job event queues for SSE
job_queues: dict[str, queue.Queue] = {}


def run_download(job_id: str, url: str, output_dir: str) -> None:
    """Run spotDL download in a background thread and push progress via queue."""
    q = job_queues[job_id]

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
        if not songs:
            pushlog("No tracks found for this URL", "warn")
            push("error", {"status": "error", "progress": 0, "message": "No tracks found for the given URL."})
            q.put(None)  # sentinel
            return

        total = len(songs)
        names = ", ".join(s.name for s in songs)
        pushlog(f"Found {total} track(s): {names}")
        push("progress", {
            "status": "downloading",
            "progress": 25,
            "message": f"Found {total} track(s). Starting download...",
        })

        pushlog(f"Downloading to {output_dir}...")
        downloader = Downloader({"output": output_dir, "simple_tui": True, "threads": 1})
        results = downloader.download_multiple_songs(songs)

        # results is List[Tuple[Song, Optional[Path]]]
        downloaded = [(song, path) for song, path in results if path is not None]
        failed     = [(song, path) for song, path in results if path is None]

        pushlog(f"Done — {len(downloaded)} downloaded, {len(failed)} failed",
                "done" if downloaded else "warn")

        if downloaded:
            names = ", ".join(song.name for song, _ in downloaded)
            push("done", {
                "status": "done",
                "progress": 100,
                "message": f"Done! Downloaded {len(downloaded)} track(s) to {output_dir}",
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
        jobs[job_id].update({"status": "error", "progress": 0, "message": str(exc)})
        job_queues[job_id].put({"event": "log",   "data": {"level": "error", "msg": str(exc)}})
        job_queues[job_id].put({"event": "error", "data": {"status": "error", "message": str(exc)}})

    finally:
        q.put(None)  # sentinel — tells the SSE generator to close


@app.route("/pick-folder")
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
    output_dir = str(Path(output).expanduser())

    if not url:
        return jsonify({"error": "url is required"}), 400

    job_id = str(uuid.uuid4())
    jobs[job_id] = {"status": "queued", "progress": 0, "message": "Queued", "output": output_dir}
    job_queues[job_id] = queue.Queue()

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


@app.route("/status/<job_id>")
def status(job_id: str):
    """Polling fallback — returns current job state as JSON."""
    if job_id not in jobs:
        return jsonify({"error": "job not found"}), 404
    return jsonify(jobs[job_id])


if __name__ == "__main__":
    app.run(debug=True, port=8080, threaded=True)
