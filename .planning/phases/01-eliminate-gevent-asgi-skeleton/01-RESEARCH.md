# Phase 1: Eliminate gevent + ASGI Skeleton - Research

**Researched:** 2026-03-05
**Domain:** Flask/gevent to FastAPI/python-socketio ASGI wiring
**Confidence:** HIGH

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| STACK-01 | Remove Flask, Flask-SocketIO, gevent, gevent-websocket, gunicorn, websocket-client, and requests from dependencies | Exact removal list verified from pyproject.toml; replacements confirmed |
| STACK-02 | Add FastAPI, python-socketio[asyncio], uvicorn, and httpx to dependencies | Version pins verified; pyproject.toml target block documented below |
| TRANS-01 | App is served via `socketio.ASGIApp` wrapping FastAPI on uvicorn (single worker) | Mount pattern verified from official python-socketio docs and example |
| TRANS-02 | All existing SocketIO event names and payload shapes preserved verbatim (zero frontend changes required) | All 6 event names catalogued from app.py with exact signatures |
</phase_requirements>

---

## Summary

Phase 1 replaces the entire server stack without changing any application logic or frontend behavior. The current `app.py` opens with `from gevent import monkey; monkey.patch_all()` on lines 1-2 — this must be the very first deletion because it corrupts the asyncio event loop at import time. Once gevent is gone, the wiring replaces Flask + Flask-SocketIO + gunicorn with FastAPI + python-socketio AsyncServer + uvicorn, connected via `socketio.ASGIApp(sio, fastapi_app)`.

This phase delivers only a skeleton: all six existing SocketIO event handlers are registered but stubbed (they log and return without doing real work). HTTP routes serve the same templates and static files. No Deepgram SDK integration occurs here — that is Phase 3. The frontend must be able to load, connect via SocketIO, and fire events without receiving 404s or connection errors. All SocketIO event names and their emitted response shapes are preserved exactly so the frontend JavaScript requires zero changes.

The dependency swap is precise: remove seven packages (flask, flask-socketio, gevent, gevent-websocket, gunicorn, websocket-client, requests), add four packages (fastapi, python-socketio[asyncio], uvicorn, httpx). The Dockerfile CMD changes from the gunicorn geventwebsocket worker invocation to `uvicorn app:app --host 0.0.0.0 --port 8080 --workers 1`. The `uv.lock` file must be regenerated after the dependency change.

**Primary recommendation:** Delete `monkey.patch_all()` first, swap dependencies in pyproject.toml second, then rewrite app.py as the ASGI skeleton — in that strict order.

---

## Standard Stack

### Core (Phase 1 changes)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `fastapi` | `>=0.115.0,<1` | HTTP routes, ASGI sub-app | Standard async Python web framework; Starlette-based StaticFiles built in |
| `python-socketio[asyncio]` | `>=5.11.0,<6` | AsyncServer + ASGIApp | Official python-socketio package; `[asyncio]` extra includes `aiohttp` for the async driver |
| `uvicorn` | `>=0.30.0,<1` | ASGI server | Standard ASGI server for FastAPI; replaces gunicorn |
| `httpx` | `>=0.27.0,<1` | Async HTTP client | Already a transitive dep of deepgram-sdk; replaces requests; needed for Phase 4 batch route |

### Packages to Keep Unchanged

| Library | Pin | Notes |
|---------|-----|-------|
| `deepgram-sdk` | `==6.0.1` | Not touched in Phase 1 (Phase 3 integration) |
| `python-dotenv` | `==1.0.0` | No change |
| `pydub` | `>=0.25.1,<0.26` | No change |
| `sounddevice` | `>=0.5.2,<0.6` | No change; used by detect_audio_settings (sync call, safe from async handler) |

### Packages to Remove

| Package | Current Pin | Reason |
|---------|-------------|--------|
| `flask` | `==3.0.0` | Replaced by FastAPI |
| `flask-socketio` | `==5.3.6` | Replaced by python-socketio |
| `gevent` | `>=23.0.0` | Root cause of asyncio incompatibility |
| `gevent-websocket` | `>=0.10.1` | Companion to gevent; eliminated with it |
| `gunicorn` | `>=21.0.0` | WSGI server; replaced by uvicorn (ASGI) |
| `websocket-client` | `>=1.8.0` | Used by stt/client.py to bypass SDK; Phase 1 stubs don't need it |
| `requests` | `>=2.32.3,<3` | Sync HTTP; replaced by httpx |

### Target pyproject.toml dependencies block

```toml
dependencies = [
    "deepgram-sdk==6.0.1",
    "fastapi>=0.115.0,<1",
    "python-socketio[asyncio]>=5.11.0,<6",
    "uvicorn>=0.30.0,<1",
    "httpx>=0.27.0,<1",
    "python-dotenv==1.0.0",
    "pydub>=0.25.1,<0.26",
    "sounddevice>=0.5.2,<0.6",
]
```

### Installation

```bash
# From /coding/deepgram-python-stt
uv sync
# uv will regenerate uv.lock after pyproject.toml change
```

---

## Architecture Patterns

### Recommended Project Structure (Phase 1 scope — files touched)

```
deepgram-python-stt/
├── app.py              # FULL REWRITE: ASGI wiring + stubbed handlers
├── pyproject.toml      # Dependency swap (7 removed, 4 added)
├── uv.lock             # Regenerated by uv sync
├── Dockerfile          # CMD line updated only
├── stt/
│   ├── client.py       # NOT TOUCHED in Phase 1 (stubs don't call it)
│   └── options.py      # NOT TOUCHED
├── common/
│   └── audio_settings.py  # NOT TOUCHED
├── templates/
│   └── index.html      # NOT TOUCHED (zero frontend changes)
└── static/             # NOT TOUCHED
```

### Pattern 1: ASGI Wiring — socketio.ASGIApp wraps FastAPI

**What:** `socketio.ASGIApp` is the single ASGI callable uvicorn serves. It routes `/socket.io/` traffic to `AsyncServer` and all other paths to the FastAPI sub-app.

**When to use:** Every python-socketio + FastAPI app. This is the only supported ASGI mount pattern.

**Example:**
```python
# Source: https://github.com/miguelgrinberg/python-socketio/blob/main/examples/server/asgi/fastapi-fiddle.py
import socketio
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",
)

fastapi_app = FastAPI()
fastapi_app.mount("/static", StaticFiles(directory="static"), name="static")

@fastapi_app.get("/")
async def index():
    return FileResponse("templates/index.html")

# THIS is what uvicorn serves — not fastapi_app
app = socketio.ASGIApp(sio, fastapi_app)
```

**uvicorn command:**
```bash
uvicorn app:app --host 0.0.0.0 --port 8001 --reload
```

Note the variable name: `app:app` means `app.py` module, `app` variable (the `ASGIApp` object). If the variable were named `socket_app`, the command would be `app:socket_app`.

### Pattern 2: Async SocketIO Event Handlers

**What:** All event handlers become `async def` coroutines. `request.sid` (Flask-SocketIO pattern) is gone — `sid` is passed as the first argument.

**Example:**
```python
# Source: python-socketio official docs + existing app.py event names
@sio.event
async def connect(sid, environ, auth=None):
    logger.info("Client connected: %s", sid)


@sio.event
async def disconnect(sid, reason=None):
    logger.info("Client disconnected: %s", sid)


@sio.on("toggle_transcription")
async def on_toggle_transcription(sid, data):
    # Phase 1: stub only — log and return
    action = data.get("action", "start")
    logger.info("[%s] toggle_transcription action=%s (stub)", sid, action)


@sio.on("audio_stream")
async def on_audio_stream(sid, data):
    # Phase 1: stub — data is bytes, drop silently
    pass


@sio.on("detect_audio_settings")
async def on_detect_audio_settings(sid):
    # detect_audio_settings() is sync CPU-bound — safe to call directly from async
    from common.audio_settings import detect_audio_settings
    try:
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
    # Phase 1: stub only
    logger.info("[%s] start_file_streaming (stub)", sid)


@sio.on("stop_file_streaming")
async def on_stop_file_streaming(sid):
    # Phase 1: stub only
    logger.info("[%s] stop_file_streaming (stub)", sid)
```

### Pattern 3: HTTP Routes in FastAPI

**What:** Flask routes (`@app.route`) become FastAPI routes (`@fastapi_app.get/post`). `request.json` becomes `await request.json()`. `jsonify` becomes `JSONResponse`.

**Example:**
```python
from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse

fastapi_app = FastAPI()

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
    data = await request.json()
    # Phase 1: stub — real httpx implementation in Phase 4
    return JSONResponse({"error": "not implemented yet"}, status_code=501)
```

### Pattern 4: Dockerfile CMD Update

**What:** Replace gunicorn + geventwebsocket worker with uvicorn directly.

**Before:**
```dockerfile
CMD ["uv", "run", "gunicorn", \
     "--worker-class", "geventwebsocket.gunicorn.workers.GeventWebSocketWorker", \
     "--workers", "1", \
     "--bind", "0.0.0.0:8080", \
     "--timeout", "120", \
     "app:app"]
```

**After:**
```dockerfile
CMD ["uv", "run", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
```

The `--workers 1` flag is explicit. Do not use `gunicorn -k uvicorn.workers.UvicornWorker` — it adds complexity without benefit for this single-machine deployment.

### Anti-Patterns to Avoid

- **Pointing uvicorn at fastapi_app:** `uvicorn app:fastapi_app` causes all `/socket.io/` WebSocket upgrades to 404. The ASGI callable MUST be the `socketio.ASGIApp` instance.
- **Using `socketio.Server` (sync) instead of `socketio.AsyncServer`:** The sync server has no `async_mode="asgi"` support. The import must be `socketio.AsyncServer`.
- **Keeping `async_mode="gevent"` from the Flask-SocketIO config:** The new server requires `async_mode="asgi"` explicitly.
- **Emitting without await:** `sio.emit(...)` without `await` silently drops the emit in async context.
- **Using `room=sid` instead of `to=sid`:** python-socketio uses `to=` keyword; `room=` is Flask-SocketIO's API and will not work.
- **Using `emit(...)` (module-level Flask-SocketIO function) instead of `await sio.emit(...)`:** The module-level emit is gone; use the `sio` object directly.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Static file serving | Custom file-reading route | `fastapi.staticfiles.StaticFiles` mount | Handles ETags, Range, MIME types correctly |
| WebSocket upgrade routing | Manual path-based dispatch | `socketio.ASGIApp(sio, fastapi_app)` | Handles polling fallback, upgrade handshake, CORS for WS |
| CORS for SocketIO | FastAPI `CORSMiddleware` | `cors_allowed_origins` param on `AsyncServer` | FastAPI CORS middleware does not handle WebSocket upgrade CORS; must be set on the SocketIO server |
| File upload parsing | Manual `request.body()` parsing | `UploadFile = File(...)` FastAPI dependency | Handles multipart streaming, size limits |
| Index HTML serving | Jinja2 template render | `FileResponse("templates/index.html")` | No templating needed; file is static Alpine.js HTML |

**Key insight:** The socket.io/HTTP routing split is handled entirely by `socketio.ASGIApp`. Any attempt to route manually (e.g., checking request path in a middleware) will break the SocketIO polling/upgrade handshake.

---

## Common Pitfalls

### Pitfall 1: gevent monkey.patch_all() fires before asyncio starts

**What goes wrong:** `app.py` lines 1-2 are `from gevent import monkey; monkey.patch_all()`. If this runs under uvicorn, it corrupts asyncio's event loop immediately. Errors manifest as startup hangs, `RuntimeError` from asgiref, or the loop dying silently on first request.

**Why it happens:** gevent replaces stdlib socket/threading/ssl modules globally at import time. asyncio depends on the unpatched versions.

**How to avoid:** Delete the two gevent lines as the absolute first edit, before any other change to `app.py`. Do not move them — delete them.

**Warning signs:** `python -c "import app"` raises an error, or uvicorn starts but hangs on first connection.

### Pitfall 2: Wrong object passed to uvicorn

**What goes wrong:** `uvicorn app:fastapi_app` instead of `uvicorn app:app`. All SocketIO connections 404 immediately. HTTP routes work fine, which makes debugging confusing.

**Why it happens:** FastAPI has no SocketIO routing built in. `socketio.ASGIApp` is the router.

**How to avoid:** Name the `socketio.ASGIApp` instance `app` in `app.py`. Double-check the uvicorn command references this name.

**Warning signs:** Browser JS console shows SocketIO 404 errors. HTTP routes return 200 correctly.

### Pitfall 3: `room=sid` vs `to=sid` in emit calls

**What goes wrong:** `await sio.emit("event", data, room=sid)` silently fails or raises a `TypeError` in python-socketio. The `room=` keyword is Flask-SocketIO's API.

**Why it happens:** python-socketio uses `to=` not `room=`. The old `socketio.emit()` calls in `app.py` all use `room=sid`.

**How to avoid:** Replace every `room=sid` with `to=sid` in all `sio.emit()` calls.

**Warning signs:** Emits appear to succeed (no exception) but clients never receive events.

### Pitfall 4: async_mode not set to "asgi"

**What goes wrong:** `socketio.AsyncServer()` without `async_mode="asgi"` defaults to "threading" mode. This produces a deadlock or events that never fire under uvicorn.

**How to avoid:** Always pass `async_mode="asgi"` explicitly: `socketio.AsyncServer(async_mode="asgi", ...)`.

### Pitfall 5: stt/client.py import still triggers at module level

**What goes wrong:** If Phase 1's new `app.py` still imports `from stt.client import STTClient`, and `stt/client.py` imports `websocket-client` (now removed), the import fails at startup.

**How to avoid:** The Phase 1 `app.py` must NOT import `stt/client.py`. Stubbed handlers have no Deepgram logic and need no STT imports. Only `stt/options.py` and `common/audio_settings.py` are safe to import in Phase 1.

**Warning signs:** `ModuleNotFoundError: No module named 'websocket'` at startup after dependency removal.

### Pitfall 6: uv.lock out of sync after pyproject.toml change

**What goes wrong:** After editing `pyproject.toml`, the old `uv.lock` still references removed packages. `uv run` or Docker build fails.

**How to avoid:** Run `uv sync` immediately after editing `pyproject.toml` to regenerate `uv.lock`. Commit both files together.

---

## Code Examples

### Complete new app.py skeleton (verified pattern)

```python
# Source: python-socketio official docs + fastapi-fiddle.py official example
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

# 1. AsyncServer — async_mode MUST be "asgi"
sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",
)

# 2. FastAPI sub-app for HTTP routes
fastapi_app = FastAPI()
fastapi_app.mount("/static", StaticFiles(directory="static"), name="static")

# 3. Combined ASGI app — THIS is what uvicorn serves
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
    # Phase 1 stub — real implementation in Phase 4
    data = await request.json()
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
    # Phase 1 stub — no Deepgram connection yet
    # Emit expected events so frontend doesn't hang
    if action == "start":
        await sio.emit("stream_started", {"request_id": None}, to=sid)
    else:
        await sio.emit("stream_finished", {"request_id": None}, to=sid)


@sio.on("audio_stream")
async def on_audio_stream(sid, data):
    # Phase 1 stub — silently drop audio chunks
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

### Development run command

```bash
cd /coding/deepgram-python-stt
uv run uvicorn app:app --host 0.0.0.0 --port 8001 --reload
```

### Smoke test sequence

```bash
# 1. Import test (must produce no errors)
uv run python -c "import app; print('import OK')"

# 2. Start server
uv run uvicorn app:app --host 0.0.0.0 --port 8001

# 3. Check HTTP root
curl -s -o /dev/null -w "%{http_code}" http://localhost:8001/
# Expected: 200

# 4. Check static assets exist
curl -s -o /dev/null -w "%{http_code}" http://localhost:8001/static/
# Expected: 200 or 404 depending on static dir contents
```

---

## Inventory: v1 SocketIO Events to Preserve

All six events from the current `app.py` must be registered in Phase 1. None are new or renamed.

| Event Name | Direction | Payload (in) | Emitted Response | Phase 1 Stub Action |
|------------|-----------|--------------|------------------|---------------------|
| `connect` | browser → server | environ dict | (none) | log only |
| `disconnect` | browser → server | reason | (none) | log only |
| `toggle_transcription` | browser → server | `{action: "start"/"stop", params: {}}` | `stream_started` or `stream_finished` | emit stub response |
| `audio_stream` | browser → server | `bytes` (binary) | (none) | drop silently |
| `detect_audio_settings` | browser → server | (none) | `audio_settings {sample_rate, channels}` | call detect_audio_settings() |
| `start_file_streaming` | browser → server | `{filename, params}` | `stream_started` | emit stub response |
| `stop_file_streaming` | browser → server | (none) | `stream_finished` | emit stub response |

**Emitted events (server → browser) that must still fire to keep frontend unblocked:**

| Event Name | Payload Shape | When Fired |
|------------|--------------|------------|
| `stream_started` | `{request_id: null}` | On toggle_transcription start, start_file_streaming |
| `stream_finished` | `{request_id: null}` | On toggle_transcription stop, stop_file_streaming |
| `audio_settings` | `{sample_rate: int, channels: int}` | On detect_audio_settings |
| `stream_error` | `{message: str}` | On any error (stub emits not needed unless error occurs) |
| `transcription_update` | `{transcript, is_final, speaker, response}` | Phase 3 only — not emitted in Phase 1 stub |

---

## State of the Art

| Old Approach | Current Approach | Impact on Phase 1 |
|--------------|------------------|-------------------|
| `gunicorn -k geventwebsocket.gunicorn.workers.GeventWebSocketWorker` | `uvicorn app:app --workers 1` | Dockerfile CMD update only |
| `from gevent import monkey; monkey.patch_all()` | Delete entirely | First edit in migration |
| `socketio.SocketIO(app, async_mode="gevent")` | `socketio.AsyncServer(async_mode="asgi")` | Core wiring change |
| `@socketio.on("event") def handler(): sid = request.sid` | `@sio.on("event") async def handler(sid, data):` | All handler signatures change |
| `socketio.emit("event", data, room=sid)` | `await sio.emit("event", data, to=sid)` | `room=` becomes `to=`; add `await` |
| `Flask(__name__)` + `send_from_directory` | `FastAPI()` + `FileResponse` / `StaticFiles` | HTTP routes rewritten |
| `request.json` (sync Flask context) | `await request.json()` | Async everywhere |
| `jsonify(...)` | `JSONResponse(...)` | FastAPI response type |

---

## Open Questions

1. **Should `toggle_transcription` stub emit `stream_started`/`stream_finished`?**
   - What we know: The frontend expects these events to unblock UI state. If the stub never emits them, the frontend may hang in a loading state.
   - What's unclear: Whether the frontend has a timeout or just waits indefinitely.
   - Recommendation: Emit the stub responses to keep frontend functional during Phase 1.

2. **Port number consistency between app.py and Dockerfile**
   - What we know: Dockerfile exposes `8080`, `PORT` env var defaults to `8080`. Current `app.py` defaults PORT to `8001`.
   - What's unclear: Which is "correct" for this project.
   - Recommendation: Keep `PORT=8001` for local dev (consistent with existing `app.py`), `8080` in Dockerfile (consistent with Fly.io config). Use `PORT = int(os.getenv("PORT", 8001))` in app.py.

---

## Sources

### Primary (HIGH confidence)
- `python-socketio` official docs: https://python-socketio.readthedocs.io/en/latest/server.html — AsyncServer, ASGIApp, async_mode
- `python-socketio` official FastAPI example: https://github.com/miguelgrinberg/python-socketio/blob/main/examples/server/asgi/fastapi-fiddle.py — canonical mount pattern
- Existing `/coding/deepgram-python-stt/app.py` — authoritative source for all event names, payload shapes, emitted responses, and current behavior
- Existing `/coding/deepgram-python-stt/pyproject.toml` — exact current dependencies to remove
- Existing `/coding/deepgram-python-stt/Dockerfile` — current CMD to replace
- `.planning/research/STACK.md` — verified dependency version pins, gotchas, pyproject.toml target block
- `.planning/research/ARCHITECTURE.md` — ASGI wiring pattern, handler signatures, emit API
- `.planning/research/PITFALLS.md` — pitfall 6 (gevent), pitfall 7 (async_mode), pitfall 12 (mount order)

### Secondary (MEDIUM confidence)
- `.planning/research/SUMMARY.md` — Phase 1 rationale, build order, risk assessment
- `.planning/STATE.md` — locked decision: gevent deletion must be first edit

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — version pins sourced from STACK.md (verified against PyPI); removal list sourced directly from pyproject.toml
- Architecture: HIGH — ASGI mount pattern verified against official python-socketio docs and official FastAPI example
- Pitfalls: HIGH — all pitfalls sourced from prior milestone research with confirmed GitHub issue references
- Event inventory: HIGH — sourced directly from current app.py (authoritative)

**Research date:** 2026-03-05
**Valid until:** 2026-04-05 (stable libraries; python-socketio, FastAPI, uvicorn APIs are stable)
