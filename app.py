import json
import os
import queue
import threading
import uuid
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

app = Flask(__name__)

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

    try:
        push("progress", {"status": "starting", "progress": 5, "message": "Initializing spotDL..."})

        from spotdl import Spotdl
        from spotdl.utils.config import SPOTDL_CONFIG

        Path(output_dir).mkdir(parents=True, exist_ok=True)

        push("progress", {"status": "searching", "progress": 15, "message": "Searching for tracks..."})

        spotdl_instance = Spotdl(
            client_id=SPOTDL_CONFIG["client_id"],
            client_secret=SPOTDL_CONFIG["client_secret"],
            downloader_settings={"output": output_dir},
        )

        songs = spotdl_instance.search([url])
        if not songs:
            push("error", {"status": "error", "progress": 0, "message": "No tracks found for the given URL."})
            q.put(None)  # sentinel
            return

        total = len(songs)
        push("progress", {
            "status": "downloading",
            "progress": 25,
            "message": f"Found {total} track(s). Starting download...",
        })

        downloaded, failed = spotdl_instance.download_songs(songs)

        if downloaded:
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
        jobs[job_id].update({"status": "error", "progress": 0, "message": str(exc)})
        job_queues[job_id].put({"event": "error", "data": {"status": "error", "message": str(exc)}})

    finally:
        q.put(None)  # sentinel — tells the SSE generator to close


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
    app.run(debug=True, port=5000, threaded=True)
