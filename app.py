from gevent import monkey
monkey.patch_all()

import logging
import os
import threading
import tempfile
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory
from flask_socketio import SocketIO, emit
from dotenv import load_dotenv
from stt.client import STTClient
from stt.options import Mode

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder="static", template_folder="templates")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="gevent")

API_KEY = os.getenv("DEEPGRAM_API_KEY", "")
PORT = int(os.getenv("PORT", 8001))
TEMP_DIR = Path(tempfile.gettempdir()) / "deepgram-stt"
TEMP_DIR.mkdir(exist_ok=True)

# Per-session state (keyed by SocketIO sid)
sessions = {}


def get_session(sid):
    if sid not in sessions:
        sessions[sid] = {
            "client": STTClient(API_KEY),
            "connection": None,
            "streaming_thread": None,
            "stop_flag": threading.Event(),
        }
    return sessions[sid]


# --- HTTP Routes ---

@app.route("/")
def index():
    return send_from_directory("templates", "index.html")


@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400
    f = request.files["file"]
    path = TEMP_DIR / f.filename
    f.save(path)
    return jsonify({"filename": f.filename, "size": path.stat().st_size})


@app.route("/transcribe", methods=["POST"])
def transcribe():
    data = request.json
    if not data:
        return jsonify({"error": "No JSON body"}), 400

    params = data.get("params", {})
    audio_source = data.get("url") or data.get("filename")

    if not audio_source:
        return jsonify({"error": "No audio source (url or filename required)"}), 400

    # If it's a filename (not a URL), resolve to temp path
    if not str(audio_source).startswith("http"):
        audio_source = str(TEMP_DIR / audio_source)

    try:
        client = STTClient(API_KEY)
        result = client.transcribe_batch(audio_source, params)
        return jsonify(result)
    except Exception as e:
        logger.error("Transcription error: %s", e)
        return jsonify({"error": str(e)}), 500


# --- SocketIO Events ---

@socketio.on("connect")
def on_connect():
    get_session(request.sid)
    logger.info("Client connected: %s", request.sid)


@socketio.on("disconnect")
def on_disconnect():
    sid = request.sid
    if sid in sessions:
        sess = sessions[sid]
        if sess["connection"]:
            sess["client"].close_stream()
        if sess["streaming_thread"] and sess["streaming_thread"].is_alive():
            sess["stop_flag"].set()
        del sessions[sid]
    logger.info("Client disconnected: %s", sid)


@socketio.on("toggle_transcription")
def on_toggle_transcription(data):
    sid = request.sid
    sess = get_session(sid)
    action = data.get("action", "start")
    params = data.get("params", {})

    logger.info("[%s] toggle_transcription action=%s params=%s", sid, action, params)

    if action == "stop":
        sess["client"].close_stream()
        sess["connection"] = None
        logger.info("[%s] Stream stopped", sid)
        return

    client = sess["client"]
    url = client.build_url(params, Mode.STREAMING)
    logger.info("[%s] Opening Deepgram stream: %s", sid, url)

    def on_transcript(result, is_final):
        # result is a plain dict from websocket-client
        alt = result.get("channel", {}).get("alternatives", [{}])[0]
        transcript = alt.get("transcript", "")
        words = alt.get("words", [])
        speaker = words[0].get("speaker") if words else None
        logger.info("[%s] Transcript (final=%s): %r", sid, is_final, transcript)
        socketio.emit("transcription_update", {
            "transcript": transcript,
            "is_final": is_final,
            "speaker": speaker,
            "response": result,
        }, room=sid)

    def on_error(err):
        logger.error("[%s] Deepgram stream error: %s", sid, err)
        socketio.emit("stream_error", {"message": err}, room=sid)

    def on_close():
        logger.info("[%s] Deepgram stream closed", sid)
        sess["connection"] = None
        socketio.emit("stream_finished", {"request_id": None}, room=sid)

    try:
        conn = client.open_stream(params, on_transcript, on_error, on_close)
        sess["connection"] = conn
        logger.info("[%s] Deepgram stream opened successfully, conn=%s", sid, type(conn).__name__)
        socketio.emit("stream_started", {"request_id": None, "url": url}, room=sid)
    except Exception as e:
        logger.error("[%s] Failed to open stream: %s", sid, e)
        socketio.emit("stream_error", {"message": str(e)}, room=sid)


@socketio.on("audio_stream")
def on_audio_stream(data):
    sid = request.sid
    sess = sessions.get(sid)
    if not sess or not sess.get("connection"):
        return
    try:
        sess["connection"].send_media(data)
    except Exception as e:
        logger.error("[%s] send_media() failed: %s", sid, e)


@socketio.on("start_file_streaming")
def on_start_file_streaming(data):
    sid = request.sid
    sess = get_session(sid)
    params = data.get("params", {})
    filename = data.get("filename", "")
    filepath = TEMP_DIR / filename

    if not filepath.exists():
        emit("stream_error", {"message": f"File not found: {filename}"})
        return

    client = sess["client"]
    url = client.build_url(params, Mode.STREAMING)
    stop_flag = sess["stop_flag"]
    stop_flag.clear()

    def on_file_transcript(result, is_final):
        alt = result.channel.alternatives[0]
        speaker = None
        if alt.words:
            speaker = getattr(alt.words[0], "speaker", None)
        try:
            response_dict = result.model_dump()
        except Exception:
            response_dict = {}
        socketio.emit("transcription_update", {
            "transcript": alt.transcript,
            "is_final": is_final,
            "speaker": speaker,
            "response": response_dict,
        }, room=sid)

    def stream_worker():
        try:
            conn = client.open_stream(params, on_file_transcript)
            sess["connection"] = conn
            socketio.emit("stream_started", {"request_id": None, "url": url}, room=sid)

            with open(filepath, "rb") as f:
                while not stop_flag.is_set():
                    chunk = f.read(4096)
                    if not chunk:
                        break
                    conn.send_media(chunk)
                    import time
                    time.sleep(0.02)  # 20ms pacing

            conn.send_close_stream()
            socketio.emit("stream_finished", {"request_id": None}, room=sid)
        except Exception as e:
            logger.error("File streaming error: %s", e)
            socketio.emit("stream_error", {"message": str(e)}, room=sid)

    t = threading.Thread(target=stream_worker, daemon=True)
    sess["streaming_thread"] = t
    t.start()


@socketio.on("stop_file_streaming")
def on_stop_file_streaming():
    sid = request.sid
    sess = sessions.get(sid)
    if sess:
        sess["stop_flag"].set()
        sess["client"].close_stream()
        sess["connection"] = None


@socketio.on("detect_audio_settings")
def on_detect_audio_settings():
    try:
        from common.audio_settings import detect_audio_settings
        settings = detect_audio_settings()
        # Normalize to the interface contract fields
        emit("audio_settings", {
            "sample_rate": int(settings.get("sample_rate", 16000)),
            "channels": int(settings.get("max_input_channels", 1)),
        })
    except Exception as e:
        logger.warning("Audio settings detection failed: %s", e)
        emit("audio_settings", {"sample_rate": 16000, "channels": 1})


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=PORT, debug=False)
