# Architecture Patterns: FastAPI + python-socketio + deepgram-sdk

**Project:** deepgram-python-stt v2.0
**Researched:** 2026-03-05
**Overall confidence:** HIGH (all claims verified against installed SDK source and official python-socketio docs)

---

## Executive Summary

The migration from Flask/gevent/websocket-client to FastAPI/asyncio/deepgram-sdk requires three coordinated changes: (1) replacing the WSGI entry point with an ASGI entry point using socketio.ASGIApp, (2) replacing the synchronous STTClient with an async context-manager wrapper around AsyncV1SocketClient, and (3) managing per-session Deepgram connections as asyncio Tasks rather than threads.

The deepgram-sdk v6.0.1 (already installed) exposes its async streaming path as `AsyncDeepgramClient.listen.v1.connect(...)`, an async context manager yielding `AsyncV1SocketClient`. That client's `start_listening()` method is a long-running coroutine that drives an event loop and emits `EventType.MESSAGE / ERROR / CLOSE` to registered callbacks. This listening loop must run as a background asyncio Task, not as a blocking call inside a SocketIO event handler.

python-socketio's `AsyncServer` integrates with FastAPI by wrapping both as a single `socketio.ASGIApp`, which uvicorn serves as one ASGI application. All SocketIO event handlers become async coroutines; out-of-band emits (from inside Deepgram callbacks) use `await sio.emit(..., to=sid)`.

---

## Recommended Architecture

```
Browser (Alpine.js)
    |  SocketIO WS + HTTP
    v
socketio.ASGIApp          <-- ASGI entry point uvicorn serves
    |           \
    |            FastAPI app (HTTP: /, /upload, /transcribe)
    |
AsyncServer (python-socketio, async_mode='asgi')
    |
    +-- on connect    -> create session dict in sio.session(sid)
    +-- on toggle_transcription (start)
    |       -> asyncio.create_task(streaming_task(sid, params))
    |       -> store task handle in sessions[sid]
    +-- on toggle_transcription (stop)
    |       -> cancel task, await close_stream
    +-- on audio_stream
    |       -> look up AsyncV1SocketClient in sessions[sid]
    |       -> await conn.send_media(data)
    +-- on disconnect -> cancel task, cleanup

streaming_task(sid, params):
    async with deepgram_client.listen.v1.connect(**params) as conn:
        sessions[sid]['conn'] = conn
        conn.on(EventType.MESSAGE, make_on_message(sid))
        conn.on(EventType.ERROR,   make_on_error(sid))
        conn.on(EventType.CLOSE,   make_on_close(sid))
        await sio.emit('stream_started', {...}, to=sid)
        await conn.start_listening()   # blocks until stream closes
    sessions[sid]['conn'] = None
    await sio.emit('stream_finished', {...}, to=sid)
```

---

## Component Boundaries

| Component | Responsibility | Communicates With |
|-----------|---------------|-------------------|
| `app.py` | ASGI wiring, HTTP routes, SocketIO event dispatch | python-socketio, FastAPI, `stt/session.py` |
| `stt/session.py` (new) | Per-session state dataclass, streaming task lifecycle | `app.py`, deepgram-sdk |
| `stt/deepgram_client.py` (replaces `stt/client.py`) | Thin async wrapper: `AsyncDeepgramClient` factory + connect helper | deepgram-sdk `AsyncV1SocketClient` |
| `stt/options.py` | Unchanged: `clean_params()`, `Mode` enum | `stt/deepgram_client.py` |
| `common/audio_settings.py` | Unchanged: detect_audio_settings() | `app.py` |

---

## Integration Point 1: python-socketio ASGI Mount with FastAPI

**Pattern:** `socketio.ASGIApp` wraps both servers into one ASGI callable.

```python
# app.py
import socketio
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')
fastapi_app = FastAPI()

# Mount static files on the FastAPI sub-app
fastapi_app.mount("/static", StaticFiles(directory="static"), name="static")

# Wrap both into one ASGI app — this is what uvicorn receives
app = socketio.ASGIApp(sio, fastapi_app)
```

**Run command:**
```
uvicorn app:app --host 0.0.0.0 --port 8001
```

**Critical constraint:** The `app` variable that uvicorn imports must be the `socketio.ASGIApp` instance, not the `fastapi_app`. If uvicorn points at `fastapi_app`, WebSocket upgrade requests for `/socket.io/` will 404.

**CORS:** Pass `cors_allowed_origins` to `AsyncServer`, not to FastAPI. python-socketio handles CORS for the Socket.IO polling/WS upgrade paths.

Confidence: HIGH — verified against [python-socketio official docs](https://python-socketio.readthedocs.io/en/latest/server.html) and the [official FastAPI example](https://github.com/miguelgrinberg/python-socketio/blob/main/examples/server/asgi/fastapi-fiddle.py).

---

## Integration Point 2: Async SocketIO Event Handlers

All handlers become `async def` coroutines. `request.sid` from Flask-SocketIO is replaced by the `sid` argument passed automatically by python-socketio.

```python
# Old (Flask-SocketIO)
@socketio.on("connect")
def on_connect():
    sid = request.sid

# New (python-socketio AsyncServer)
@sio.event
async def connect(sid, environ):
    ...

@sio.event
async def disconnect(sid, reason):
    ...

@sio.on("toggle_transcription")
async def on_toggle_transcription(sid, data):
    ...

@sio.on("audio_stream")
async def on_audio_stream(sid, data):
    ...
```

**Emit from within a handler:**
```python
await sio.emit("stream_started", {"request_id": None}, to=sid)
```

**Emit from outside a handler (e.g., inside a Deepgram callback):**
```python
# sio is the AsyncServer instance (module-level singleton)
await sio.emit("transcription_update", payload, to=sid)
```

Since `_emit_async` in `EventEmitterMixin` awaits callbacks that return awaitables, a Deepgram MESSAGE callback defined as `async def` will be awaited correctly inside `start_listening()`. This means `sio.emit()` can be awaited directly inside the callback. Confidence: HIGH — verified in installed `core/events.py`.

---

## Integration Point 3: deepgram-sdk Async Streaming

The installed deepgram-sdk v6.0.1 exposes streaming via:

```python
async with client.listen.v1.connect(model="nova-3", **other_params) as conn:
    # conn is AsyncV1SocketClient
    conn.on(EventType.MESSAGE, callback)
    conn.on(EventType.ERROR, error_callback)
    conn.on(EventType.CLOSE, close_callback)
    await conn.start_listening()   # long-running; exits when WebSocket closes
```

**Verified API surface (from installed source `listen/v1/socket_client.py` and `listen/v1/client.py`):**

| Method | Signature | Notes |
|--------|-----------|-------|
| `connect(...)` | `@asynccontextmanager` on `AsyncV1Client` | Keyword-only params match Deepgram API |
| `conn.on(event, cb)` | `EventEmitterMixin.on()` — sync registration | Both sync and async callbacks work |
| `conn.start_listening()` | `async def` — long-running coroutine | Emits OPEN, then MESSAGE per frame, then CLOSE |
| `conn.send_media(data)` | `async def`, accepts `bytes` | Direct WebSocket send |
| `conn.send_close_stream()` | `async def` | Sends `{"type": "CloseStream"}` |
| `conn.send_keep_alive()` | `async def` | Sends `{"type": "KeepAlive"}` |

**Parameter mapping from options.py to SDK:**

The `AsyncV1Client.connect()` takes keyword-only arguments matching Deepgram API param names (`model`, `language`, `encoding`, `sample_rate`, `diarize`, `interim_results`, `punctuate`, `smart_format`, `vad_events`, `endpointing`, `utterance_end_ms`, etc.). These map directly to the dict produced by `clean_params()`, so the existing `options.py` `clean_params()` output can be unpacked with `**` into `connect()`. All values must be strings (`Optional[str]` typed), so booleans/ints from the frontend need `str()` coercion.

**Message type dispatching:** `start_listening()` internally parses incoming JSON using `construct_type()` into a `Union[ListenV1Results, ListenV1Metadata, ListenV1UtteranceEnd, ListenV1SpeechStarted]` discriminated union. The `EventType.MESSAGE` callback receives a typed Pydantic object, not a raw dict. This means the callback no longer needs to do `data.get("channel", {}).get("alternatives", ...)` — it accesses typed fields directly.

---

## Per-Session State with asyncio

**Replace:** `sessions: dict` with threading primitives (`stop_flag: threading.Event`, `streaming_thread: threading.Thread`)

**With:** A simple dict of per-session state, keyed by `sid`, with an `asyncio.Task` handle instead of a thread:

```python
# Module-level (app.py or stt/session.py)
sessions: dict[str, dict] = {}

# On connect
sessions[sid] = {
    "conn": None,        # AsyncV1SocketClient when stream is active
    "task": None,        # asyncio.Task for streaming_task
}

# On toggle_transcription start
task = asyncio.create_task(streaming_task(sid, params))
sessions[sid]["task"] = task

# On toggle_transcription stop / disconnect
task = sessions[sid].get("task")
if task and not task.done():
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
```

**Why task cancellation works:** When `task.cancel()` is called, asyncio raises `CancelledError` at the next `await` inside the task — which will be inside `conn.start_listening()` during `await self._websocket.recv()`. The `async with websockets_client_connect(...)` context manager in the SDK will then call `close()` on the WebSocket as part of its `__aexit__`. This means the CloseStream handshake may or may not complete gracefully — for a cleaner shutdown, send `await conn.send_close_stream()` before cancelling the task.

**Recommended stop sequence:**
```python
conn = sessions[sid].get("conn")
if conn:
    await conn.send_close_stream()
    # give Deepgram ~500ms to send final results
    await asyncio.sleep(0.5)
task = sessions[sid].get("task")
if task and not task.done():
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
```

**Thread-safety note:** python-socketio's AsyncServer runs all event handlers in the same asyncio event loop (uvicorn's loop). There are no threads. The `sessions` dict is accessed only from coroutines within that single event loop — no locks needed. This is simpler than the v1 gevent model.

---

## Data Flow: Audio Chunk Through the System

```
Browser MediaRecorder
  -- binary WebM chunk via SocketIO 'audio_stream' event -->
AsyncServer.on('audio_stream', sid, data)
  -- sessions[sid]['conn'].send_media(data) -->
AsyncV1SocketClient._websocket.send(data)   [websockets lib, async]
  -- WSS binary frame -->
Deepgram API
  -- WSS JSON Results frame -->
AsyncV1SocketClient.start_listening() loop
  -- _emit_async(EventType.MESSAGE, ListenV1Results) -->
on_message(result) callback [registered per-session]
  -- await sio.emit('transcription_update', payload, to=sid) -->
Browser SocketIO client
  -- transcript displayed
```

---

## Streaming Task Implementation Pattern

```python
# stt/session.py (suggested new file)

async def streaming_task(sid: str, params: dict, sio, deepgram_client, sessions):
    """
    Long-running asyncio Task: owns the Deepgram WebSocket for one browser session.
    Registered callbacks close over `sid` and `sio` to emit back to the browser.
    """
    from deepgram.core.events import EventType
    from stt.options import clean_params, Mode

    clean = clean_params(params, Mode.STREAMING)
    # SDK requires str values; coerce booleans and ints
    str_params = {k: str(v).lower() if isinstance(v, bool) else str(v)
                  for k, v in clean.items()}

    try:
        async with deepgram_client.listen.v1.connect(**str_params) as conn:
            sessions[sid]["conn"] = conn

            async def on_message(result):
                # result is ListenV1Results (typed Pydantic object)
                if not hasattr(result, "channel"):
                    return
                alt = result.channel.alternatives[0]
                transcript = alt.transcript
                is_final = bool(transcript) and bool(result.is_final)
                speaker = alt.words[0].speaker if alt.words else None
                await sio.emit("transcription_update", {
                    "transcript": transcript,
                    "is_final": is_final,
                    "speaker": speaker,
                    "response": result.dict(),
                }, to=sid)

            async def on_error(exc):
                await sio.emit("stream_error", {"message": str(exc)}, to=sid)

            async def on_close(_):
                sessions[sid]["conn"] = None

            conn.on(EventType.MESSAGE, on_message)
            conn.on(EventType.ERROR, on_error)
            conn.on(EventType.CLOSE, on_close)

            await sio.emit("stream_started", {"request_id": None}, to=sid)
            await conn.start_listening()

    except asyncio.CancelledError:
        raise   # let asyncio propagate
    except Exception as exc:
        await sio.emit("stream_error", {"message": str(exc)}, to=sid)
    finally:
        sessions[sid]["conn"] = None
        sessions[sid]["task"] = None
        await sio.emit("stream_finished", {"request_id": None}, to=sid)
```

---

## HTTP Routes in FastAPI

The three HTTP routes (`/`, `/upload`, `/transcribe`) replace Flask equivalents with FastAPI:

```python
from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse

fastapi_app = FastAPI()

@fastapi_app.get("/")
async def index():
    return FileResponse("templates/index.html")

@fastapi_app.post("/upload")
async def upload(file: UploadFile = File(...)):
    ...

@fastapi_app.post("/transcribe")
async def transcribe(request: Request):
    data = await request.json()
    ...
```

The `/transcribe` batch route currently uses the synchronous `requests` library (in `STTClient.transcribe_batch()`). In the async version, replace with `httpx.AsyncClient` — or use the SDK's `AsyncDeepgramClient.listen.v1.media.transcribe(...)` REST method. The `httpx` approach is simpler and avoids event loop blocking.

---

## New vs Modified Components

### New Files

| File | What It Is |
|------|-----------|
| `stt/session.py` | `streaming_task()` coroutine + `SessionState` dataclass |
| `stt/deepgram_client.py` | `AsyncDeepgramClient` factory (singleton creation + config) |

### Modified Files

| File | Change |
|------|--------|
| `app.py` | Full rewrite: Flask -> FastAPI, Flask-SocketIO -> AsyncServer, gevent -> asyncio |
| `stt/client.py` | Delete or gut; sync streaming path replaced by `streaming_task()` |
| `stt/options.py` | No functional change; may need bool-to-str coercion helper added |
| `requirements.txt` / `pyproject.toml` | Add: `fastapi`, `python-socketio`, `uvicorn[standard]`, `httpx`; Remove: `flask`, `flask-socketio`, `gevent`, `websocket-client` |
| `Dockerfile` | Change CMD from `python app.py` to `uvicorn app:app --host 0.0.0.0 --port 8001` |

### Unchanged Files

| File | Reason |
|------|--------|
| `stt/options.py` | `clean_params()` logic reusable as-is |
| `common/audio_settings.py` | Sync CPU-bound function, fine to call from async handler |
| `templates/index.html` | Frontend unchanged (zero JS changes required) |
| `static/` | Unchanged |

---

## Build Order

1. **ASGI wiring skeleton** — Create new `app.py` with `socketio.ASGIApp(sio, fastapi_app)`, bare HTTP routes serving index.html, and stubbed SocketIO event handlers that just log. Confirm uvicorn starts and browser can load the page and connect via SocketIO.

2. **deepgram_client factory** — Create `stt/deepgram_client.py`: `AsyncDeepgramClient(api_key=...)` singleton (module-level or app startup). Confirm `client.listen.v1` is accessible. Write a standalone async test that opens a connection and sends one audio chunk.

3. **streaming_task + per-session state** — Implement `streaming_task()` in `stt/session.py`. Wire `toggle_transcription` start/stop and `audio_stream` handlers. Test with real mic audio end-to-end.

4. **File streaming** — Port `on_start_file_streaming`/`on_stop_file_streaming` using the same `streaming_task()` with an `asyncio.Task` that reads the file in chunks with `asyncio.sleep(0.02)` pacing.

5. **Batch transcription** — Replace `STTClient.transcribe_batch()` with `httpx.AsyncClient` or SDK media client in the `/transcribe` route.

6. **Test suite** — Update `tests/test_app.py` for async SocketIO test client (`python-socketio[asyncio_client]`), mock `deepgram_client.listen.v1.connect` as an async context manager.

---

## Anti-Patterns to Avoid

### Anti-Pattern 1: Blocking the Event Loop in a SocketIO Handler

**What:** Calling `conn.start_listening()` directly inside `on_toggle_transcription` without wrapping in a Task.

**Why bad:** `start_listening()` is a long-running coroutine. It will block the asyncio event loop, making the server unable to handle any other SocketIO events (including `audio_stream`) for the duration of the stream — which is the entire session.

**Instead:** `asyncio.create_task(streaming_task(sid, params))` and return immediately from the handler.

### Anti-Pattern 2: Using `asyncio.run()` or `loop.run_until_complete()` Inside a Handler

**What:** Trying to nest event loops for sync/async bridging.

**Why bad:** uvicorn runs a single asyncio event loop. `asyncio.run()` creates a new loop and will raise `RuntimeError: This event loop is already running`.

**Instead:** Everything in handlers must be `async def` and awaited; no sync-to-async bridging needed.

### Anti-Pattern 3: Sharing One `AsyncDeepgramClient` Across Sessions Without Care

**What:** Having a single global `AsyncDeepgramClient` and assuming its state is per-connection.

**Why bad:** `AsyncDeepgramClient` is a stateless HTTP/WS factory — sharing it is fine. But the `AsyncV1SocketClient` yielded by `connect()` is per-connection and must live inside the streaming task's context manager scope. Do not store the client wrapper across the context manager boundary.

**Instead:** Store `AsyncV1SocketClient` (the `conn` object) in `sessions[sid]`, not the factory client.

### Anti-Pattern 4: Calling `sio.emit()` Without `await` in Async Context

**What:** `socketio.emit(...)` (the old Flask-SocketIO sync API) vs `await sio.emit(...)`.

**Why bad:** python-socketio's `AsyncServer.emit()` is a coroutine. Calling it without `await` silently drops the emit.

**Instead:** Always `await sio.emit(...)` in all async handlers and callbacks.

### Anti-Pattern 5: Passing Raw dict to SDK connect() When Types Expect str

**What:** `client.listen.v1.connect(diarize=True, sample_rate=16000)` — passing Python bool/int.

**Why bad:** `AsyncV1Client.connect()` parameters are typed `Optional[str]`. Passing non-string values may result in incorrect query string serialization (e.g., `True` instead of `true`).

**Instead:** Convert to strings: `diarize="true"`, `sample_rate="16000"`. Add a coercion step in `stt/options.py` or the session helper.

---

## Scalability Considerations

| Concern | Single user (demo) | Notes |
|---------|-------------------|-------|
| Per-session Deepgram WS | One Task per session | Tasks are lightweight; no thread-per-session overhead |
| Audio throughput | Direct `await conn.send_media(data)` | No buffering needed; asyncio handles backpressure |
| Memory | `sessions` dict grows with connected clients | Add disconnect cleanup; no other concern for demo scale |
| File streaming | `asyncio.sleep(0.02)` pacing in a Task | Same pattern as v1 threading; works fine |

---

## Sources

- Installed deepgram-sdk v6.0.1 source: `/coding/deepgram-python-stt/.venv/lib/python3.12/site-packages/deepgram/` (HIGH confidence — authoritative)
  - `listen/v1/socket_client.py` — `AsyncV1SocketClient` API
  - `listen/v1/client.py` — `AsyncV1Client.connect()` async context manager
  - `core/events.py` — `EventType`, `EventEmitterMixin._emit_async()`
  - `client.py` — `AsyncDeepgramClient` constructor
- [python-socketio official docs — Server](https://python-socketio.readthedocs.io/en/latest/server.html) (HIGH confidence — official)
- [python-socketio FastAPI example](https://github.com/miguelgrinberg/python-socketio/blob/main/examples/server/asgi/fastapi-fiddle.py) (HIGH confidence — official example)
- Existing `app.py` and `stt/client.py` in this repo (HIGH confidence — source of truth for v1 behavior)
