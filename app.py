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
    """Return a list of segments ready to synthesize, in reading order.

    Each segment is a dict with voice, text, and trim_start/trim_end flags that
    mark a language switch in the middle of a line. There the silence edge-tts
    puts around every clip is trimmed, so Thai and English flow together.
    """
    if english not in ENGLISH_VOICES:
        return [new_segment(VOICE, chunk) for chunk in split_text(text)]
    segments = []
    line_break = True  # whether a line/paragraph break precedes the next chunk
    for lang, part in split_languages(text):
        voice = ENGLISH_VOICES[english] if lang == "en" else VOICE
        chunks = [c for c in split_text(part) if SPEAKABLE.search(c)]
        if not chunks:
            line_break = line_break or "\n" in part
            continue
        if re.match(r"[ \t]*\n", part):
            line_break = True
        for index, chunk in enumerate(chunks):
            inline = index == 0 and not line_break and bool(segments)
            prev = segments[-1] if segments else None
            if inline and prev["voice"] == voice and (
                len(prev["text"]) + len(chunk) + 1 <= MAX_CHUNK_CHARS
            ):
                prev["text"] += " " + chunk
                continue
            if inline:
                prev["trim_end"] = True
            segments.append(new_segment(voice, chunk, trim_start=inline))
            line_break = False
        line_break = bool(re.search(r"\n[ \t]*$", part))
    return segments


def new_segment(voice, text, trim_start=False):
    return {"voice": voice, "text": text, "trim_start": trim_start, "trim_end": False}


# --- MP3 frame trimming (no re-encoding, no ffmpeg needed) -------------------

LEAD_SECONDS = 0.05  # silence kept before speech at a language switch
TAIL_SECONDS = 0.12  # silence kept after speech at a language switch
TAIL_SENTENCE_SECONDS = 0.35  # ...when the clip ends a sentence (. ! ?)
MP3_BITRATES = {
    1: [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],
    2: [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
}
MP3_SAMPLE_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}


def mp3_frames(data):
    """Yield (offset, size, seconds) for each MPEG Layer III frame in data."""
    i = 0
    if data[:3] == b"ID3" and len(data) >= 10:
        i = 10 + ((data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9])
    while i + 4 <= len(data):
        header = int.from_bytes(data[i : i + 4], "big")
        version = (header >> 19) & 3
        layer = (header >> 17) & 3
        bitrate_index = (header >> 12) & 0xF
        rate_index = (header >> 10) & 3
        if (
            (header >> 21) != 0x7FF
            or version == 1
            or layer != 1
            or bitrate_index in (0, 15)
            or rate_index == 3
        ):
            i += 1
            continue
        mpeg1 = version == 3
        sample_rate = MP3_SAMPLE_RATES[version][rate_index]
        bitrate = MP3_BITRATES[1 if mpeg1 else 2][bitrate_index] * 1000
        size = (144 if mpeg1 else 72) * bitrate // sample_rate + ((header >> 9) & 1)
        yield i, size, (1152 if mpeg1 else 576) / sample_rate
        i += size


def trim_mp3(data, start, end):
    """Keep only whole frames overlapping [start, end) seconds."""
    out = bytearray()
    t = 0.0
    for offset, size, seconds in mp3_frames(data):
        if t + seconds > start and t < end:
            out += data[offset : offset + size]
        t += seconds
    return bytes(out) if out else data


def trim_segment_audio(segment, audio, speech):
    if not speech or not (segment["trim_start"] or segment["trim_end"]):
        return audio
    speech_start, speech_end = speech
    start = speech_start - LEAD_SECONDS if segment["trim_start"] else 0.0
    tail = TAIL_SENTENCE_SECONDS if re.search(r"[.!?]$", segment["text"]) else TAIL_SECONDS
    end = speech_end + tail if segment["trim_end"] else float("inf")
    return trim_mp3(audio, max(0.0, start), end)


async def synthesize_chunk(text, voice, rate):
    """Return (mp3_bytes, (speech_start, speech_end) in seconds or None)."""
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    audio = bytearray()
    starts, ends = [], []
    async for message in communicate.stream():
        if message["type"] == "audio":
            audio.extend(message["data"])
        elif message["type"] in ("SentenceBoundary", "WordBoundary"):
            starts.append(message["offset"] / 1e7)  # 100-ns units -> seconds
            ends.append((message["offset"] + message["duration"]) / 1e7)
    if not audio:
        raise RuntimeError("ไม่ได้รับเสียงจากบริการ edge-tts")
    return bytes(audio), ((min(starts), max(ends)) if starts else None)


async def synthesize_all(job, segments, rate):
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def one(segment):
        async with semaphore:
            for attempt in range(1, RETRIES + 1):
                try:
                    audio, speech = await synthesize_chunk(
                        segment["text"], segment["voice"], rate
                    )
                    break
                except Exception:
                    if attempt == RETRIES:
                        raise
                    await asyncio.sleep(2 * attempt)
            job["done"] += 1
            return trim_segment_audio(segment, audio, speech)

    return await asyncio.gather(*(one(segment) for segment in segments))


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
