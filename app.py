import logging
import os
import tempfile
from pathlib import Path

import socketio
from dotenv import load_dotenv
from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PORT = int(os.getenv("PORT", 8001))
TEMP_DIR = Path(tempfile.gettempdir()) / "deepgram-stt"
TEMP_DIR.mkdir(exist_ok=True)

# 1. AsyncServer — async_mode MUST be "asgi" (not "gevent", not "threading")
sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",
)

# 2. FastAPI sub-app for HTTP routes only
fastapi_app = FastAPI()
fastapi_app.mount("/static", StaticFiles(directory="static"), name="static")

# 3. Combined ASGI callable — THIS is what uvicorn serves, not fastapi_app
app = socketio.ASGIApp(sio, fastapi_app)


# --- HTTP Routes ---

@fastapi_app.get("/")
async def index():
    return FileResponse("templates/index.html")


@fastapi_app.post("/upload")
async def upload(file: UploadFile = File(...)):
    path = TEMP_DIR / file.filename
    content = await file.read()
    path.write_bytes(content)
    return JSONResponse({"filename": file.filename, "size": path.stat().st_size})


@fastapi_app.post("/transcribe")
async def transcribe(request: Request):
    # Phase 1 stub — real httpx implementation in Phase 4
    await request.json()
    return JSONResponse({"error": "transcribe not yet implemented"}, status_code=501)


# --- SocketIO Event Handlers ---

@sio.event
async def connect(sid, environ, auth=None):
    logger.info("Client connected: %s", sid)


@sio.event
async def disconnect(sid, reason=None):
    logger.info("Client disconnected: %s", sid)


@sio.on("toggle_transcription")
async def on_toggle_transcription(sid, data):
    action = data.get("action", "start")
    logger.info("[%s] toggle_transcription action=%s (stub)", sid, action)
    # Emit expected lifecycle events so the frontend does not hang in loading state
    if action == "start":
        await sio.emit("stream_started", {"request_id": None}, to=sid)
    else:
        await sio.emit("stream_finished", {"request_id": None}, to=sid)


@sio.on("audio_stream")
async def on_audio_stream(sid, data):
    # Phase 1 stub — audio chunks are silently dropped
    pass


@sio.on("detect_audio_settings")
async def on_detect_audio_settings(sid):
    try:
        from common.audio_settings import detect_audio_settings
        settings = detect_audio_settings()
        await sio.emit("audio_settings", {
            "sample_rate": int(settings.get("sample_rate", 16000)),
            "channels": int(settings.get("max_input_channels", 1)),
        }, to=sid)
    except Exception as e:
        logger.warning("Audio settings detection failed: %s", e)
        await sio.emit("audio_settings", {"sample_rate": 16000, "channels": 1}, to=sid)


@sio.on("start_file_streaming")
async def on_start_file_streaming(sid, data):
    logger.info("[%s] start_file_streaming (stub)", sid)
    await sio.emit("stream_started", {"request_id": None}, to=sid)


@sio.on("stop_file_streaming")
async def on_stop_file_streaming(sid):
    logger.info("[%s] stop_file_streaming (stub)", sid)
    await sio.emit("stream_finished", {"request_id": None}, to=sid)
