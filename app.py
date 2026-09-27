"""Thai text -> MP3 web app using edge-tts (th-TH-PremwadeeNeural).

Long text is split into chunks, each chunk is synthesized separately, and the
MP3 pieces are joined into one file. English runs inside the text can be read
by an English voice, because the Thai voice mispronounces English words. Work runs in a background thread so the
browser can poll for progress instead of waiting on one long request.
"""

import asyncio
import os
import re
import threading
import time
import uuid

import edge_tts
from flask import Flask, Response, abort, jsonify, render_template, request

VOICE = "th-TH-PremwadeeNeural"
ENGLISH_VOICES = {
    "us": "en-US-JennyNeural",
    "gb": "en-GB-SoniaNeural",
}
DEFAULT_ENGLISH = "us"
CONCURRENCY = 4
MAX_CHUNK_CHARS = 1500
MAX_TEXT_CHARS = int(os.environ.get("MAX_TEXT_CHARS", "100000"))
JOB_TTL_SECONDS = 60 * 60
RETRIES = 3

app = Flask(__name__)

jobs = {}
jobs_lock = threading.Lock()


def split_text(text, max_chars=MAX_CHUNK_CHARS):
    """Split text into chunks of at most max_chars.

    Thai rarely uses full stops, so we break on paragraph/line breaks first,
    then on spaces (which Thai uses between phrases/sentences), and only cut
    mid-run as a last resort.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    pieces = []
    for para in re.split(r"\n+", text):
        para = re.sub(r"[ \t]+", " ", para).strip()
        if not para:
            continue
        if len(para) <= max_chars:
            pieces.append(para)
            continue
        for word in re.split(r"(?<=[ .!?。…])", para):
            while len(word) > max_chars:
                pieces.append(word[:max_chars])
                word = word[max_chars:]
            if word:
                pieces.append(word)

    chunks = []
    current = ""
    for piece in pieces:
        sep = "\n" if current and not current.endswith(" ") else ""
        if current and len(current) + len(sep) + len(piece) > max_chars:
            chunks.append(current.strip())
            current = piece
        else:
            current += sep + piece
    if current.strip():
        chunks.append(current.strip())
    return chunks


# A run of English: starts with a Latin letter and may continue with Latin
# letters, digits, spaces and common punctuation, e.g. "iPhone 15 Pro, Apple".
ENGLISH_RUN = re.compile(r"[A-Za-z][A-Za-z0-9'’&.,:;!?%()\-/+#@_ \t]*")
SPEAKABLE = re.compile(r"[0-9A-Za-z\u0E00-\u0E7F]")


def split_languages(text):
    """Split text into [(lang, text), ...] where lang is "th" or "en"."""
    segments = []
    pos = 0
    for match in ENGLISH_RUN.finditer(text):
        run = match.group().rstrip(" \t,:;(-/")
        start, end = match.start(), match.start() + len(run)
        if start > pos:
            segments.append(("th", text[pos:start]))
        segments.append(("en", run))
        pos = end
    if pos < len(text):
        segments.append(("th", text[pos:]))
    return segments


def build_segments(text, english):
    """Return [(voice, text), ...] ready to synthesize, in reading order."""
    if english not in ENGLISH_VOICES:
        return [(VOICE, chunk) for chunk in split_text(text)]
    result = []
    for lang, part in split_languages(text):
        voice = ENGLISH_VOICES[english] if lang == "en" else VOICE
        for chunk in split_text(part):
            if not SPEAKABLE.search(chunk):
                continue
            if result and result[-1][0] == voice and (
                len(result[-1][1]) + len(chunk) + 1 <= MAX_CHUNK_CHARS
            ):
                result[-1] = (voice, result[-1][1] + " " + chunk)
            else:
                result.append((voice, chunk))
    return result


async def synthesize_chunk(text, voice, rate):
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    audio = bytearray()
    async for message in communicate.stream():
        if message["type"] == "audio":
            audio.extend(message["data"])
    if not audio:
        raise RuntimeError("ไม่ได้รับเสียงจากบริการ edge-tts")
    return bytes(audio)


async def synthesize_all(job, segments, rate):
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def one(voice, text):
        async with semaphore:
            for attempt in range(1, RETRIES + 1):
                try:
                    audio = await synthesize_chunk(text, voice, rate)
                    break
                except Exception:
                    if attempt == RETRIES:
                        raise
                    await asyncio.sleep(2 * attempt)
            job["done"] += 1
            return audio

    return await asyncio.gather(*(one(voice, text) for voice, text in segments))


def run_job(job_id, segments, rate):
    job = jobs[job_id]
    try:
        parts = asyncio.run(synthesize_all(job, segments, rate))
        # edge-tts returns MP3 frames with identical settings (24 kHz mono) for
        # every voice, so plain concatenation yields one valid MP3 file.
        job["audio"] = b"".join(parts)
        job["status"] = "finished"
    except Exception as exc:  # noqa: BLE001 - report any failure to the user
        job["status"] = "error"
        job["error"] = f"แปลงเสียงไม่สำเร็จ: {exc}"


def cleanup_old_jobs():
    cutoff = time.time() - JOB_TTL_SECONDS
    with jobs_lock:
        for job_id in [j for j, v in jobs.items() if v["created"] < cutoff]:
            del jobs[job_id]


@app.get("/")
def index():
    return render_template("index.html", max_chars=MAX_TEXT_CHARS)


@app.post("/api/jobs")
def create_job():
    cleanup_old_jobs()
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify(error="กรุณาวางข้อความก่อน"), 400
    if len(text) > MAX_TEXT_CHARS:
        return jsonify(error=f"ข้อความยาวเกิน {MAX_TEXT_CHARS:,} ตัวอักษร"), 400

    try:
        speed = int(data.get("speed", 0))
    except (TypeError, ValueError):
        speed = 0
    speed = max(-50, min(100, speed))
    rate = f"{speed:+d}%"

    english = data.get("english", DEFAULT_ENGLISH)
    segments = build_segments(text, english)
    if not segments:
        return jsonify(error="ไม่พบข้อความที่อ่านออกเสียงได้"), 400
    job_id = uuid.uuid4().hex
    with jobs_lock:
        jobs[job_id] = {
            "status": "running",
            "done": 0,
            "total": len(segments),
            "audio": None,
            "error": None,
            "created": time.time(),
        }
    threading.Thread(target=run_job, args=(job_id, segments, rate), daemon=True).start()
    return jsonify(id=job_id, total=len(segments))


@app.get("/api/jobs/<job_id>")
def job_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify(error="ไม่พบงานนี้ (อาจหมดอายุแล้ว)"), 404
    return jsonify(
        status=job["status"],
        done=job["done"],
        total=job["total"],
        error=job["error"],
        size=len(job["audio"]) if job["audio"] else 0,
    )


@app.get("/api/jobs/<job_id>/audio")
def job_audio(job_id):
    job = jobs.get(job_id)
    if not job or not job["audio"]:
        abort(404)
    audio = job["audio"]
    headers = {"Cache-Control": "no-store"}
    if request.args.get("download"):
        filename = time.strftime("thai-tts-%Y%m%d-%H%M%S.mp3")
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'

    # Support Range requests: Safari on iPad needs them to play/seek audio.
    range_header = request.headers.get("Range")
    match = re.match(r"bytes=(\d*)-(\d*)", range_header or "")
    if match:
        size = len(audio)
        start = int(match.group(1)) if match.group(1) else 0
        end = int(match.group(2)) if match.group(2) else size - 1
        if not match.group(1) and match.group(2):
            start, end = size - int(match.group(2)), size - 1
        end = min(end, size - 1)
        if start > end or start >= size:
            return Response(status=416, headers={"Content-Range": f"bytes */{size}"})
        headers.update(
            {
                "Content-Range": f"bytes {start}-{end}/{size}",
                "Accept-Ranges": "bytes",
            }
        )
        return Response(audio[start : end + 1], 206, headers, mimetype="audio/mpeg")

    headers["Accept-Ranges"] = "bytes"
    return Response(audio, 200, headers, mimetype="audio/mpeg")


@app.get("/healthz")
def healthz():
    return "ok"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "7860")), debug=True)
