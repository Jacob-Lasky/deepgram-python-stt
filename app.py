import asyncio
import logging
import os
import re
import tempfile
from pathlib import Path

from mutagen import File as MutagenFile

import httpx
import socketio
from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType
from deepgram.listen.v1.types import ListenV1Results, ListenV1Metadata
from dotenv import load_dotenv
from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from stt.options import clean_params, Mode

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

def _clean_error(e: Exception) -> str:
    """Strip SDK request headers (including auth token) from Deepgram exception messages."""
    msg = str(e)
    m = re.search(r'status_code:\s*(\d+),\s*body:\s*(.+)$', msg, re.DOTALL)
    if m:
        return f"Deepgram {m.group(1)}: {m.group(2).strip()}"
    return msg


# Per-session state — module-level dict, not sio.session() (too slow for audio hot path)
# Key: SocketIO session id (sid)
# Value: dict with keys: task (asyncio.Task), stop_event (asyncio.Event),
#        ws (AsyncV1SocketClient | None), request_id (str | None)
_sessions: dict[str, dict] = {}


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


@fastapi_app.get("/files/{filename}")
async def serve_file(filename: str):
    path = TEMP_DIR / filename
    if not path.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path)


@fastapi_app.post("/api/tts-transcribe")
async def tts_transcribe(request: Request):
    body = await request.json()
    text = body.get("text", "").strip()
    tts_model = body.get("tts_model", "aura-2-asteria-en")
    stt_params = body.get("stt_params", {})

    if not text:
        return JSONResponse({"error": "text is required"}, status_code=400)

    api_key = os.getenv("DEEPGRAM_API_KEY", "")
    headers = {"Authorization": f"Token {api_key}"}

    clean = clean_params(stt_params, Mode.BATCH)
    query_params = {}
    for k, v in clean.items():
        if isinstance(v, bool):
            query_params[k] = "true" if v else "false"
        elif isinstance(v, (list, str)):
            query_params[k] = v
        else:
            query_params[k] = str(v)
    query_params.setdefault("model", "nova-2")

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            # Step 1: TTS — convert text to MP3
            tts_resp = await client.post(
                "https://api.deepgram.com/v1/speak",
                headers={**headers, "Content-Type": "application/json"},
                params={"model": tts_model, "encoding": "mp3"},
                json={"text": text},
            )
            tts_resp.raise_for_status()
            audio_bytes = tts_resp.content

            # Step 2: STT — transcribe the generated audio
            stt_resp = await client.post(
                "https://api.deepgram.com/v1/listen",
                headers={**headers, "Content-Type": "audio/mp3"},
                params=query_params,
                content=audio_bytes,
            )
            stt_resp.raise_for_status()
            return JSONResponse(stt_resp.json())
    except httpx.HTTPStatusError as e:
        return JSONResponse({"error": str(e)}, status_code=e.response.status_code)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@fastapi_app.post("/transcribe")
async def transcribe(request: Request):
    body = await request.json()
    params = body.get("params", {})
    url = body.get("url")
    filename = body.get("filename")

    if not url and not filename:
        return JSONResponse({"error": "url or filename required"}, status_code=400)

    api_key = os.getenv("DEEPGRAM_API_KEY", "")

    # Build clean query params for batch mode, convert bools to lowercase strings
    clean = clean_params(params, Mode.BATCH)
    query_params = {}
    for k, v in clean.items():
        if isinstance(v, bool):
            query_params[k] = "true" if v else "false"
        elif isinstance(v, (list, str)):
            query_params[k] = v
        else:
            query_params[k] = str(v)
    query_params.setdefault("model", "nova-2")

    headers = {"Authorization": f"Token {api_key}"}

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            if url:
                resp = await client.post(
                    "https://api.deepgram.com/v1/listen",
                    headers={**headers, "Content-Type": "application/json"},
                    params=query_params,
                    json={"url": url},
                )
            else:
                file_path = TEMP_DIR / filename
                if not file_path.exists():
                    return JSONResponse({"error": "File not found"}, status_code=404)
                file_bytes = file_path.read_bytes()
                resp = await client.post(
                    "https://api.deepgram.com/v1/listen",
                    headers={**headers, "Content-Type": "audio/*"},
                    params=query_params,
                    content=file_bytes,
                )
            resp.raise_for_status()
            return JSONResponse(resp.json())
    except httpx.HTTPStatusError as e:
        return JSONResponse({"error": str(e)}, status_code=e.response.status_code)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# --- Helper functions ---

def _params_to_sdk_kwargs(raw_params: dict) -> dict:
    """Convert frontend params dict to deepgram-sdk 6.x keyword args.
    model is required by connect() — default to nova-2 if not provided.
    """
    clean = clean_params(raw_params, Mode.STREAMING)
    kwargs = {}
    for k, v in clean.items():
        if isinstance(v, bool):
            kwargs[k] = "true" if v else "false"
        elif isinstance(v, (list, str)):
            kwargs[k] = v
        else:
            kwargs[k] = str(v)
    kwargs.setdefault("model", "nova-2")
    return kwargs


# --- Streaming Task ---

async def streaming_task(sid: str, params: dict, stop_event: asyncio.Event) -> None:
    """Owns the Deepgram WebSocket lifecycle for one SocketIO session.
    Runs as an asyncio.Task. Emits stream_started, transcription_update, stream_finished.
    """
    api_key = os.getenv("DEEPGRAM_API_KEY", "")
    dg = AsyncDeepgramClient(api_key=api_key)
    sdk_kwargs = _params_to_sdk_kwargs(params)

    try:
        async with dg.listen.v1.connect(**sdk_kwargs) as ws:
            # Store ws so on_audio_stream can call ws.send_media()
            if sid in _sessions:
                _sessions[sid]["ws"] = ws

            async def on_message(msg, **kwargs):
                logger.debug("[%s] on_message type=%s", sid, type(msg).__name__)
                if isinstance(msg, ListenV1Metadata):
                    if sid in _sessions:
                        _sessions[sid]["request_id"] = msg.request_id
                elif isinstance(msg, ListenV1Results):
                    transcript = msg.channel.alternatives[0].transcript
                    is_final = bool(msg.is_final)
                    await sio.emit("transcription_update", {
                        "transcript": transcript,
                        "is_final": is_final,
                    }, to=sid)

            ws.on(EventType.MESSAGE, on_message)
            listen_task = asyncio.create_task(ws.start_listening())

            # Emit stream_started immediately — don't gate on Metadata arrival
            await sio.emit("stream_started", {"request_id": None}, to=sid)

            # Keep-alive loop — sends every 8s (under Deepgram's ~10s idle timeout)
            async def keep_alive_loop():
                while not stop_event.is_set():
                    await asyncio.sleep(8)
                    if not stop_event.is_set():
                        try:
                            await ws.send_keep_alive()
                        except Exception as e:
                            logger.warning("[%s] keep_alive error: %s", sid, e)
                            break

            ka_task = asyncio.create_task(keep_alive_loop())

            # Wait for stop signal from on_toggle_transcription(stop) or disconnect()
            await stop_event.wait()

            # Graceful shutdown: cancel keep-alive, send CloseStream, await final results
            ka_task.cancel()
            try:
                await ws.send_close_stream()
                await listen_task  # blocks until Deepgram flushes final Results + closes
            except (asyncio.CancelledError, Exception) as e:
                logger.warning("[%s] Error during graceful shutdown: %s", sid, e)
                if not listen_task.done():
                    listen_task.cancel()

    except Exception as e:
        logger.error("[%s] streaming_task error: %s", sid, e)
        _sessions.pop(sid, None)  # Free slot before notifying client so retries aren't blocked
        await sio.emit("stream_error", {"message": _clean_error(e)}, to=sid)
    finally:
        request_id = _sessions[sid].get("request_id") if sid in _sessions else None
        await sio.emit("stream_finished", {"request_id": request_id}, to=sid)
        _sessions.pop(sid, None)
        logger.info("[%s] streaming_task finished, session cleaned up", sid)


# --- File Streaming Task ---

CHUNK_SIZE = 4096


async def file_streaming_task(
    sid: str, filename: str, params: dict, stop_event: asyncio.Event
) -> None:
    """Streams an uploaded file to Deepgram over WebSocket.
    Mirrors streaming_task() but reads from a local file instead of waiting on stop_event.
    Emits stream_started, transcription_update, stream_finished.
    """
    api_key = os.getenv("DEEPGRAM_API_KEY", "")
    dg = AsyncDeepgramClient(api_key=api_key)
    sdk_kwargs = _params_to_sdk_kwargs(params)
    file_path = TEMP_DIR / filename

    try:
        async with dg.listen.v1.connect(**sdk_kwargs) as ws:
            # Store ws reference in session
            if sid in _sessions:
                _sessions[sid]["ws"] = ws

            async def on_message(msg, **kwargs):
                logger.debug("[%s] file on_message type=%s", sid, type(msg).__name__)
                if isinstance(msg, ListenV1Metadata):
                    if sid in _sessions:
                        _sessions[sid]["request_id"] = msg.request_id
                elif isinstance(msg, ListenV1Results):
                    transcript = msg.channel.alternatives[0].transcript
                    is_final = bool(msg.is_final)
                    await sio.emit("transcription_update", {
                        "transcript": transcript,
                        "is_final": is_final,
                        "start": msg.start,
                    }, to=sid)

            ws.on(EventType.MESSAGE, on_message)
            listen_task = asyncio.create_task(ws.start_listening())

            # Emit stream_started immediately — same pattern as streaming_task
            await sio.emit("stream_started", {"request_id": None}, to=sid)

            # Compute real-time pacing: sleep between chunks so Deepgram
            # receives audio at 1x speed, keeping transcripts in sync with playback.
            try:
                audio_info = MutagenFile(file_path)
                duration = audio_info.info.length if audio_info else None
            except Exception:
                duration = None
            file_size = file_path.stat().st_size
            sleep_per_chunk = (CHUNK_SIZE / file_size * duration) if duration and file_size else 0

            # Stream file in chunks; stop early if stop_event set
            try:
                with open(file_path, "rb") as f:
                    while not stop_event.is_set():
                        chunk = f.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        await ws.send_media(chunk)
                        if sleep_per_chunk:
                            await asyncio.sleep(sleep_per_chunk)
            except FileNotFoundError:
                await sio.emit("stream_error", {"message": f"File not found: {filename}"}, to=sid)
                # Graceful shutdown even on FileNotFoundError
                try:
                    await ws.send_close_stream()
                    await listen_task
                except (asyncio.CancelledError, Exception) as e:
                    logger.warning("[%s] Error during file-not-found shutdown: %s", sid, e)
                    if not listen_task.done():
                        listen_task.cancel()
                return

            # EOF reached (or stop_event set) — flush final words (STR-04 pattern)
            try:
                await ws.send_close_stream()
                await listen_task  # blocks until Deepgram flushes final Results + closes
            except (asyncio.CancelledError, Exception) as e:
                logger.warning("[%s] Error during file streaming graceful shutdown: %s", sid, e)
                if not listen_task.done():
                    listen_task.cancel()

    except Exception as e:
        logger.error("[%s] file_streaming_task error: %s", sid, e)
        _sessions.pop(sid, None)
        await sio.emit("stream_error", {"message": _clean_error(e)}, to=sid)
    finally:
        request_id = _sessions[sid].get("request_id") if sid in _sessions else None
        await sio.emit("stream_finished", {"request_id": request_id}, to=sid)
        _sessions.pop(sid, None)
        logger.info("[%s] file_streaming_task finished, session cleaned up", sid)


# --- SocketIO Event Handlers ---

@sio.event
async def connect(sid, environ, auth=None):
    logger.info("Client connected: %s", sid)


@sio.event
async def disconnect(sid, reason=None):
    session = _sessions.pop(sid, None)
    if session:
        session["stop_event"].set()
        task = session.get("task")
        if task and not task.done():
            task.cancel()
    logger.info("Client disconnected: %s reason=%s", sid, reason)


@sio.on("toggle_transcription")
async def on_toggle_transcription(sid, data):
    action = data.get("action", "start")
    params = data.get("params", data.get("config", {}))
    logger.info("[%s] toggle_transcription action=%s", sid, action)

    if action == "start":
        if sid in _sessions:
            logger.warning("[%s] toggle_transcription(start) while already streaming — ignoring", sid)
            return
        stop_event = asyncio.Event()
        _sessions[sid] = {"stop_event": stop_event, "ws": None, "request_id": None}
        task = asyncio.create_task(streaming_task(sid, params, stop_event))
        _sessions[sid]["task"] = task

    elif action == "stop":
        if sid not in _sessions:
            # Not streaming — keep frontend in sync
            await sio.emit("stream_finished", {"request_id": None}, to=sid)
            return
        _sessions[sid]["stop_event"].set()
        # stream_finished is emitted by streaming_task after listen_task completes


@sio.on("audio_stream")
async def on_audio_stream(sid, data):
    session = _sessions.get(sid)
    if session and session.get("ws") is not None:
        try:
            audio = data if isinstance(data, bytes) else bytes(data)
            await session["ws"].send_media(audio)
        except Exception as e:
            logger.warning("[%s] send_media error: %s", sid, e)
    # If ws is None (WebSocket not yet open), drop silently — browser buffers more audio


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
    filename = data.get("filename") if data else None
    params = data.get("params", {}) if data else {}
    logger.info("[%s] start_file_streaming filename=%s", sid, filename)

    if not filename:
        await sio.emit("stream_error", {"message": "filename is required"}, to=sid)
        return

    if sid in _sessions:
        logger.warning("[%s] start_file_streaming while already streaming — ignoring", sid)
        return

    stop_event = asyncio.Event()
    _sessions[sid] = {"stop_event": stop_event, "ws": None, "request_id": None}
    task = asyncio.create_task(file_streaming_task(sid, filename, params, stop_event))
    _sessions[sid]["task"] = task


@sio.on("stop_file_streaming")
async def on_stop_file_streaming(sid, data=None):
    logger.info("[%s] stop_file_streaming", sid)

    if sid not in _sessions:
        # Not streaming — keep frontend in sync
        await sio.emit("stream_finished", {"request_id": None}, to=sid)
        return

    _sessions[sid]["stop_event"].set()
    # stream_finished is emitted by file_streaming_task after listen_task completes
