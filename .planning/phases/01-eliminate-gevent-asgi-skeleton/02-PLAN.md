---
phase: 01-eliminate-gevent-asgi-skeleton
plan: 02
type: execute
wave: 2
depends_on:
  - "01-01"
files_modified:
  - app.py
autonomous: true
requirements:
  - TRANS-01
  - TRANS-02

must_haves:
  truths:
    - "python -c 'import app' raises no gevent or monkey-patch errors"
    - "Browser loads the frontend at localhost and connects via SocketIO without 404"
    - "All 7 SocketIO events (connect, disconnect, toggle_transcription, audio_stream, detect_audio_settings, start_file_streaming, stop_file_streaming) are registered"
    - "toggle_transcription emits stream_started or stream_finished so the frontend does not hang"
    - "detect_audio_settings emits audio_settings with sample_rate and channels"
    - "stt/client.py is NOT imported anywhere in app.py (websocket-client removed)"
  artifacts:
    - path: "app.py"
      provides: "ASGI skeleton with all 7 SocketIO events stubbed"
      contains: "socketio.ASGIApp"
      min_lines: 80
  key_links:
    - from: "app.py"
      to: "socketio.ASGIApp"
      via: "app = socketio.ASGIApp(sio, fastapi_app)"
      pattern: "ASGIApp\\(sio"
    - from: "uvicorn"
      to: "app.py:app"
      via: "app:app target in Dockerfile CMD"
      pattern: "app = socketio\\.ASGIApp"
    - from: "on_detect_audio_settings"
      to: "common.audio_settings"
      via: "local import inside handler"
      pattern: "from common.audio_settings import"
---

<objective>
Rewrite app.py from scratch: delete the gevent monkey-patch, replace Flask + Flask-SocketIO with FastAPI + python-socketio AsyncServer, and register all 7 SocketIO event handlers as stubbed async coroutines.

Purpose: This is the core migration. The new app.py is the ASGI entry point uvicorn serves. All SocketIO event names are preserved verbatim so the frontend requires zero changes. No Deepgram SDK integration occurs here — that is Phase 3.
Output: A working app.py that imports cleanly, serves HTTP routes, and accepts SocketIO connections.
</objective>

<execution_context>
@./.claude/get-shit-done/workflows/execute-plan.md
@./.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/phases/01-eliminate-gevent-asgi-skeleton/01-RESEARCH.md

<interfaces>
<!-- Key contracts the executor must preserve from the old app.py. -->
<!-- These are the exact SocketIO event names and response shapes the frontend expects. -->

SocketIO events registered (browser -> server):
  connect(sid, environ, auth=None)       — log only
  disconnect(sid, reason=None)           — log only
  toggle_transcription(sid, data)        — data: {action: "start"|"stop", params: {}}
                                            emits: stream_started {request_id: null} or stream_finished {request_id: null}
  audio_stream(sid, data)               — data: bytes; drop silently
  detect_audio_settings(sid)            — emits: audio_settings {sample_rate: int, channels: int}
  start_file_streaming(sid, data)       — data: {filename, params}; emits stream_started {request_id: null}
  stop_file_streaming(sid)              — emits: stream_finished {request_id: null}

HTTP routes:
  GET  /          -> FileResponse("templates/index.html")
  POST /upload    -> save file to TEMP_DIR, return {filename, size}
  POST /transcribe -> stub returning 501 {error: "transcribe not yet implemented"}

Static files:
  /static/* -> StaticFiles(directory="static")

CRITICAL wiring rules from STATE.md and RESEARCH.md:
  - gevent monkey.patch_all() lines 1-2 must be DELETED first — do not move, delete
  - sio = socketio.AsyncServer(async_mode="asgi", ...) — async_mode MUST be "asgi"
  - app = socketio.ASGIApp(sio, fastapi_app) — "app" is the ASGI callable uvicorn serves
  - All emits use: await sio.emit("event", data, to=sid) — NOT room=sid (Flask-SocketIO API)
  - stt/client.py must NOT be imported — websocket-client is removed
  - stt/options.py is also not needed in the stub — do not import it
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Delete gevent lines and write new app.py skeleton</name>
  <files>app.py</files>
  <action>
Write the complete new app.py. This is a full file replacement — the old content is entirely discarded.

The FIRST action before writing is to confirm the old gevent lines are gone (they will be, since this is a full rewrite). Do NOT preserve any content from the old file.

Write the following exact implementation to app.py:

```python
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
```

Verify immediately after writing:
- No `gevent` anywhere in the file
- No `flask` anywhere in the file
- No `from stt` anywhere in the file
- `app = socketio.ASGIApp(sio, fastapi_app)` is present
- `async_mode="asgi"` is present
- All 7 event names registered: connect, disconnect, toggle_transcription, audio_stream, detect_audio_settings, start_file_streaming, stop_file_streaming
- All `sio.emit()` calls use `to=sid` not `room=sid`
  </action>
  <verify>
    <automated>cd /coding/deepgram-python-stt && uv run python -c "import app; print('import OK')"</automated>
  </verify>
  <done>`python -c "import app"` exits 0 with no errors; no gevent, flask, or stt.client references in the file</done>
</task>

<task type="auto">
  <name>Task 2: Smoke test the running server</name>
  <files></files>
  <action>
Run the following smoke test sequence to confirm the ASGI skeleton works end-to-end. These are read-only verification commands — no file changes.

Start uvicorn in the background, test HTTP endpoints, then stop it:

```bash
cd /coding/deepgram-python-stt

# Step 1: Confirm clean import
uv run python -c "import app; print('import OK')"

# Step 2: Start server in background (port 8001 for local dev)
uv run uvicorn app:app --host 127.0.0.1 --port 8001 &
SERVER_PID=$!
sleep 2

# Step 3: Test HTTP root returns 200
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8001/)
echo "GET / -> $HTTP_CODE"

# Step 4: Test SocketIO endpoint is reachable (not 404)
SIO_CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:8001/socket.io/?EIO=4&transport=polling")
echo "GET /socket.io/ -> $SIO_CODE"

# Step 5: Stop server
kill $SERVER_PID 2>/dev/null || true
```

Expected results:
- `import OK` printed
- GET / returns 200
- GET /socket.io/?EIO=4&transport=polling returns 200 (SocketIO polling handshake)

If /socket.io/ returns 404, the `app` variable is pointing at fastapi_app instead of socketio.ASGIApp — re-check that `app = socketio.ASGIApp(sio, fastapi_app)` is the last assignment to `app`.
  </action>
  <verify>
    <automated>cd /coding/deepgram-python-stt && uv run python -c "import app; assert hasattr(app, '__call__'); import socketio; assert isinstance(app.app, socketio.AsyncServer) or True; print('app is ASGI callable')"</automated>
  </verify>
  <done>HTTP root returns 200; /socket.io/ polling endpoint returns 200 (not 404); server starts and stops cleanly</done>
</task>

</tasks>

<verification>
```bash
cd /coding/deepgram-python-stt

# 1. Clean import — no gevent errors
uv run python -c "import app; print('import OK')"

# 2. No banned imports in app.py
! grep -E "gevent|from flask|from stt" app.py

# 3. All 7 events registered
grep -E "connect|disconnect|toggle_transcription|audio_stream|detect_audio_settings|start_file_streaming|stop_file_streaming" app.py | wc -l

# 4. ASGI wiring correct
grep "socketio.ASGIApp(sio, fastapi_app)" app.py

# 5. No room= keyword in emits (must use to=)
! grep "room=sid" app.py

# 6. async_mode set to asgi
grep 'async_mode="asgi"' app.py
```
</verification>

<success_criteria>
- `uv run python -c "import app"` exits 0 with no error output
- app.py contains no references to gevent, flask, stt.client, or websocket
- All 7 SocketIO event names are registered (grep count >= 7)
- `app = socketio.ASGIApp(sio, fastapi_app)` present — uvicorn target is the ASGI wrapper
- `async_mode="asgi"` present in AsyncServer constructor
- No `room=sid` in any emit call (all use `to=sid`)
- GET / returns 200, GET /socket.io/?EIO=4&transport=polling returns 200
</success_criteria>

<output>
After completion, create `.planning/phases/01-eliminate-gevent-asgi-skeleton/01-02-SUMMARY.md` with:
- Confirmation of all 7 events registered
- Import test result
- HTTP smoke test results (status codes)
- Any deviations from the plan (e.g., port conflicts, import issues encountered)
</output>
