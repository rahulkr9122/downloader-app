import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import uuid
from flask import Flask, jsonify, render_template, request, redirect, url_for, Response, after_this_request, send_file
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config["SECRET_KEY"] = "change-this-secret"

SUPPORTED_DOMAINS = ["youtube.com", "youtu.be", "instagram.com", "instagr.am"]
download_progress = {}
progress_lock = threading.Lock()


def is_supported_url(url: str) -> bool:
    lowered = url.lower()
    return any(domain in lowered for domain in SUPPORTED_DOMAINS)


def create_download_id() -> str:
    return str(uuid.uuid4())


def extract_info(url: str) -> dict:
    cmd = [sys.executable, "-m", "yt_dlp", "--no-playlist", "--dump-json", url]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Unable to retrieve video info")
    return json.loads(result.stdout)


def get_filename(info: dict) -> str:
    title = info.get("title", "video")
    ext = info.get("ext", "mp4")
    filename = f"{title}.{ext}"
    return secure_filename(filename) or "video.mp4"


def build_format_options(info: dict) -> list[dict]:
    formats = []
    seen = set()

    for fmt in info.get("formats", []):
        format_id = fmt.get("format_id")
        if not format_id or format_id in seen:
            continue
        seen.add(format_id)

        vcodec = fmt.get("vcodec")
        acodec = fmt.get("acodec")
        if vcodec == "none" or acodec == "none":
            continue

        height = fmt.get("height")
        if height is None:
            continue

        formats.append(fmt)

    best_by_height = {}
    for fmt in formats:
        height = fmt.get("height") or 0
        current = best_by_height.get(height)
        if current is None or (fmt.get("tbr") or 0) > (current.get("tbr") or 0):
            best_by_height[height] = fmt

    heights = sorted(best_by_height.keys(), reverse=True)
    options = []

    if heights:
        best_format = best_by_height[heights[0]]
        options.append({"id": best_format["format_id"], "label": "Best available"})

    preferred_heights = [1080, 720, 480, 360]
    added = {heights[0]} if heights else set()

    for target in preferred_heights:
        for h in heights:
            if h <= target and h not in added:
                fmt = best_by_height[h]
                options.append({"id": fmt["format_id"], "label": f"{h}p"})
                added.add(h)
                break

    for h in heights:
        if len(options) >= 5:
            break
        if h not in added:
            fmt = best_by_height[h]
            options.append({"id": fmt["format_id"], "label": f"{h}p"})
            added.add(h)

    if not options:
        options.append({"id": "best", "label": "Best available"})

    return options


def format_size(size: int | None) -> str:
    if not size:
        return ""
    for unit in ["B", "KiB", "MiB", "GiB"]:
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TiB"


def init_progress(download_id: str, filename: str) -> None:
    with progress_lock:
        download_progress[download_id] = {
            "status": "ready",
            "filename": filename,
            "percent": 0,
            "downloaded": "0B",
            "total": "unknown",
            "speed": "0B/s",
            "eta": "--:--",
            "message": "Waiting to start",
        }


def parse_progress_line(line: str, download_id: str) -> None:
    progress = download_progress.get(download_id)
    if not progress:
        return

    match = re.search(
        r"\[download\]\s+(?P<percent>[0-9.]+)%.*?of\s+(?P<total>[0-9.]+[KMGiB]+).*?at\s+(?P<speed>[0-9.]+[KMGiB]+/s).*?ETA\s+(?P<eta>[0-9:]+)",
        line,
    )
    if match:
        progress["status"] = "downloading"
        progress["percent"] = float(match.group("percent"))
        progress["total"] = match.group("total")
        progress["speed"] = match.group("speed")
        progress["eta"] = match.group("eta")
        progress["downloaded"] = f"{match.group('percent')}%"
        progress["message"] = "Downloading"
    elif "100%" in line and "in" in line:
        progress["percent"] = 100.0
        progress["status"] = "completed"
        progress["message"] = "Download complete"


def progress_reader(process: subprocess.Popen, download_id: str) -> None:
    if not process.stderr:
        return
    for raw_line in process.stderr:
        try:
            line = raw_line.decode("utf-8", errors="replace")
        except AttributeError:
            line = raw_line
        parse_progress_line(line, download_id)


def download_to_temp_file(url: str, fmt: str, download_id: str) -> tuple[str, str]:
    temp_dir = tempfile.mkdtemp()
    output_template = os.path.join(temp_dir, "video.%(ext)s")

    specific_format = fmt or "best"
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-playlist",
        "--newline",
        "-f",
        specific_format,
        "-o",
        output_template,
        url,
    ]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    reader_thread = threading.Thread(target=progress_reader, args=(process, download_id), daemon=True)
    reader_thread.start()

    try:
        retcode = process.wait()
        if retcode != 0:
            stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
            failed_message = stderr.strip() or "Download failed"
            progress = download_progress.get(download_id)
            if progress:
                progress.update({"status": "failed", "message": failed_message, "eta": "--:--"})
            raise RuntimeError(failed_message)

        temp_file = None
        for entry in os.listdir(temp_dir):
            if entry.lower().endswith(".mp4"):
                temp_file = os.path.join(temp_dir, entry)
                break

        if not temp_file or not os.path.exists(temp_file):
            raise RuntimeError("Downloaded file not found")

        progress = download_progress.get(download_id)
        if progress:
            progress.update({"status": "completed", "percent": 100.0, "message": "Download complete", "eta": "00:00"})

        return temp_file, temp_dir
    finally:
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()
        read_thread_alive = reader_thread.is_alive()
        if read_thread_alive:
            reader_thread.join(timeout=1)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/prepare")
def prepare():
    url = request.args.get("url", "", type=str).strip()
    if not url:
        return redirect(url_for("index"))

    if not is_supported_url(url):
        return render_template("index.html", error="Only YouTube and Instagram URLs are supported.")

    try:
        info = extract_info(url)
    except RuntimeError as exc:
        return render_template("index.html", error=str(exc))

    filename = get_filename(info)
    download_id = create_download_id()
    init_progress(download_id, filename)
    formats = build_format_options(info)
    return render_template(
        "prepare.html",
        filename=filename,
        download_url=url_for("download"),
        progress_id=download_id,
        formats=formats,
    )


@app.route("/download")
def download():
    url = request.args.get("url", "", type=str).strip()
    fmt = request.args.get("format_id", default="", type=str)
    download_id = request.args.get("download_id", "", type=str)

    if not url or not download_id:
        return redirect(url_for("index"))

    if not is_supported_url(url):
        return render_template("index.html", error="Only YouTube and Instagram URLs are supported.")

    progress = download_progress.get(download_id)
    if not progress:
        return render_template("index.html", error="Download session expired.")

    temp_path, temp_dir = download_to_temp_file(url, fmt, download_id)

    with open(temp_path, "rb") as f:
        data = f.read()

    mimetype = "application/octet-stream"
    ext = os.path.splitext(temp_path)[1].lower()
    if ext == ".mp4":
        mimetype = "video/mp4"
    elif ext == ".mkv":
        mimetype = "video/x-matroska"
    elif ext == ".webm":
        mimetype = "video/webm"

    filename = progress["filename"]

    try:
        response = Response(data, mimetype=mimetype)
        response.headers["Content-Disposition"] = f"attachment; filename=\"{filename}\""
        return response
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            if os.path.isdir(temp_dir):
                os.rmdir(temp_dir)
        except OSError:
            pass


@app.route("/download_status/<download_id>")
def download_status(download_id: str):
    status = download_progress.get(download_id)
    if not status:
        return jsonify({"error": "Unknown download ID"}), 404
    return jsonify(status)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
