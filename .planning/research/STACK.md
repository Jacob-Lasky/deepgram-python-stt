# Technology Stack: Flask to FastAPI Migration

**Project:** deepgram-python-stt v2 migration
**Researched:** 2026-03-05
**Confidence:** HIGH — based on direct inspection of installed deepgram-sdk 6.0.1 source, official python-socketio docs, and verified PyPI version data.

---

## What Changes (and Why)

The v1 stack used gevent monkey-patching to make Flask work asynchronously. This is fundamentally incompatible with deepgram-sdk 6.x, which uses the `websockets` library natively over asyncio. The fix is to swap to a stack that is asyncio-native from the ground up.

---

## Packages to REMOVE

| Package | Why Remove |
|---------|------------|
| `flask==3.0.0` | Replaced by FastAPI. Flask is a sync-first framework that requires gevent patches to do async. |
| `flask-socketio==5.3.6` | Replaced by `python-socketio`. Flask-SocketIO's async mode still depends on gevent. |
| `gevent>=23.0.0` | The entire reason this existed was to monkey-patch Flask into fake-async. Eliminated. |
| `gevent-websocket>=0.10.1` | Companion to gevent for WSGI WebSocket upgrades. Eliminated with gevent. |
| `gunicorn>=21.0.0` | Gunicorn is a WSGI server. Replaced by uvicorn (ASGI). |
| `websocket-client>=1.8.0` | The raw WebSocket library used in `stt/client.py` to bypass the SDK. Eliminated once deepgram-sdk is used properly. |
| `requests>=2.32.3,<3` | Used for batch transcription HTTP calls. Can be replaced by `httpx` (async-native), which deepgram-sdk already depends on internally. |

---

## Packages to ADD

| Package | Version Pin | Purpose | Why |
|---------|-------------|---------|-----|
| `fastapi` | `>=0.115.0,<1` | Web framework + HTTP routing | Async-native, ASGI, used as the "other_asgi_app" that python-socketio wraps. Static file serving via Starlette's `StaticFiles`. |
| `python-socketio` | `>=5.11.0,<6` | Async SocketIO server | Provides `socketio.AsyncServer(async_mode='asgi')` and `socketio.ASGIApp`. Replaces Flask-SocketIO. Version 5.16.0 is latest (Dec 2025). |
| `uvicorn` | `>=0.30.0,<1` | ASGI server | Replaces gunicorn. Runs the combined SocketIO+FastAPI ASGI app. Standard for FastAPI. Latest: 0.41.0. |
| `httpx` | `>=0.27.0,<1` | Async HTTP client | Replaces `requests` for batch transcription. deepgram-sdk already depends on httpx internally; adding it explicitly pins the version for the batch transcription path. Optional if you keep a sync requests call in a threadpool. |

**Packages to KEEP unchanged:**

| Package | Notes |
|---------|-------|
| `deepgram-sdk==6.0.1` | Already installed. Pin exactly — this is the SDK version whose async API is documented below. |
| `python-dotenv==1.0.0` | No change needed. |
| `pydub>=0.25.1,<0.26` | Used for batch audio processing. Keep. |
| `sounddevice>=0.5.2,<0.6` | Used for audio settings detection. Keep. |

---

## deepgram-sdk 6.0.1 Async Streaming API

This is the authoritative description, verified by reading the installed SDK source at `.venv/lib/python3.12/site-packages/deepgram/`.

### Client Entry Points

```python
from deepgram import DeepgramClient, AsyncDeepgramClient
from deepgram.core.events import EventType
from deepgram.listen.v1.types import ListenV1Results, ListenV1Metadata
```

`DeepgramClient` is for sync/threaded use. `AsyncDeepgramClient` is for asyncio. Both have identical `.listen.v1.connect()` APIs — one is a `@contextmanager`, the other is an `@asynccontextmanager`.

### Async Streaming: `AsyncDeepgramClient.listen.v1.connect()`

```python
client = AsyncDeepgramClient(api_key="...")

async with client.listen.v1.connect(
    model="nova-3",
    encoding="webm-opus",      # pass all as strings
    interim_results="true",
    punctuate="true",
    diarize="false",
    language="en",
) as connection:  # yields AsyncV1SocketClient
    # Register event callbacks BEFORE start_listening
    connection.on(EventType.MESSAGE, on_message_handler)
    connection.on(EventType.ERROR, on_error_handler)
    connection.on(EventType.CLOSE, on_close_handler)

    # Start the receive loop (blocks until connection closes)
    await connection.start_listening()
```

**Important:** All query parameters are typed as `Optional[str]` in this SDK version, even booleans like `interim_results`. Pass `"true"` / `"false"` as strings, not Python booleans.

### `AsyncV1SocketClient` Methods

Sourced directly from `/deepgram/listen/v1/socket_client.py`:

| Method | Signature | Description |
|--------|-----------|-------------|
| `send_media` | `async def send_media(message: bytes) -> None` | Send a binary audio chunk |
| `send_close_stream` | `async def send_close_stream(message=None) -> None` | Send `{"type": "CloseStream"}` control message |
| `send_keep_alive` | `async def send_keep_alive(message=None) -> None` | Send `{"type": "KeepAlive"}` to prevent timeout |
| `send_finalize` | `async def send_finalize(message=None) -> None` | Send `{"type": "Finalize"}` to flush interim results |
| `recv` | `async def recv() -> V1SocketClientResponse` | Receive one message (use instead of start_listening for manual loop) |
| `on` | `def on(event_name: EventType, callback: Callable) -> None` | Register event listener |
| `start_listening` | `async def start_listening()` | Run receive loop, emits OPEN/MESSAGE/ERROR/CLOSE events |

### Event Types and Message Types

```python
from deepgram.core.events import EventType

EventType.OPEN    # connection established
EventType.MESSAGE # any message received (ListenV1Results, ListenV1Metadata, etc.)
EventType.ERROR   # exception occurred
EventType.CLOSE   # connection closed
```

The `MESSAGE` payload is a union type: `ListenV1Results | ListenV1Metadata | ListenV1UtteranceEnd | ListenV1SpeechStarted`.

Discriminate by checking `.type` or `isinstance()`:

```python
from deepgram.listen.v1.types import ListenV1Results

async def on_message(result):
    if isinstance(result, ListenV1Results):
        alt = result.channel.alternatives[0]
        transcript = alt.transcript
        is_final = result.is_final
```

### Batch Transcription via SDK

The SDK's `AsyncDeepgramClient.listen.v1.media.transcribe_url()` and `transcribe_file()` replace the manual `requests.post()` calls in the current `stt/client.py`. These are straightforward async REST calls.

---

## python-socketio + FastAPI ASGI Integration

Verified against python-socketio official docs and the example at `miguelgrinberg/python-socketio`.

### Setup Pattern

```python
import socketio
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

# 1. Create the async Socket.IO server
sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",   # or list of origins
)

# 2. Create FastAPI app for HTTP routes
fastapi_app = FastAPI()
fastapi_app.mount("/static", StaticFiles(directory="static"), name="static")

@fastapi_app.get("/")
async def index():
    ...

# 3. Wrap both into a single ASGI app
# socket.io handles /socket.io/ traffic; FastAPI handles everything else
app = socketio.ASGIApp(sio, fastapi_app)
```

**Critical:** The object passed to uvicorn MUST be the `socketio.ASGIApp` (`app`), NOT the `fastapi_app`. If you point uvicorn at `fastapi_app`, Socket.IO connections will 404.

### Event Handlers

```python
@sio.event
async def connect(sid, environ, auth):
    pass

@sio.event
async def disconnect(sid):
    pass

@sio.on("toggle_transcription")
async def handle_toggle(sid, data):
    pass

@sio.on("audio_stream")
async def handle_audio(sid, data):  # data is bytes
    pass
```

### Emitting to Clients

```python
await sio.emit("stream_started", {}, to=sid)
await sio.emit("transcription_update", {"transcript": text, "is_final": True}, to=sid)
```

---

## Asyncio/SocketIO Threading Gotchas

These are the critical patterns to get right in the new implementation:

### Gotcha 1: `start_listening()` Blocks the Event Loop

`await connection.start_listening()` is a blocking receive loop. If called directly inside a Socket.IO event handler, it will block the entire asyncio event loop, preventing any other SocketIO events (including `audio_stream` chunks) from being processed.

**Fix:** Run it as a background task:
```python
asyncio.create_task(connection.start_listening())
```

Or use `sio.start_background_task()` which is the python-socketio-idiomatic way:
```python
sio.start_background_task(deepgram_listen_loop, connection)
```

### Gotcha 2: `threading.Event` Blocks the Async Loop

The current `stt/client.py` uses `threading.Thread` and `threading.Lock`. In the new async stack, use `asyncio.Event` and `asyncio.Lock` instead. `threading.Event.wait()` blocks the event loop and prevents SocketIO heartbeats from firing.

### Gotcha 3: Emit from Non-SocketIO Context

When emitting from inside a deepgram callback (which runs as part of an asyncio task, not inside a SocketIO handler), use the `sio` object directly with `await sio.emit(...)`. The `sio` reference is accessible because it's module-level. This is safe in ASGI mode.

### Gotcha 4: Single Worker Constraint

python-socketio in ASGI mode does not share state between processes. Fly.io should be configured with `processes = 1` (or min/max machines = 1) for the web service to avoid split-brain. This was already implicit with the gevent single-process model.

### Gotcha 5: `cors_allowed_origins` Must Match Client Origin

In production on Fly.io, set `cors_allowed_origins` explicitly to the app's domain or `"*"`. Mismatch causes silent connection failures — the WebSocket upgrades fail with a CORS rejection that looks like a generic disconnect.

---

## uvicorn Configuration

### Development

```bash
uvicorn app:app --host 0.0.0.0 --port 5000 --reload
```

Note: `app:app` refers to the `socketio.ASGIApp` object named `app`, not the FastAPI app.

### Production (Dockerfile)

```dockerfile
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5000", "--workers", "1"]
```

Use `--workers 1` explicitly. Multiple uvicorn workers do not share the Socket.IO state (same as the single-process constraint above). For the Fly.io single-machine deployment this app uses, one worker is correct.

Do NOT use `gunicorn -k uvicorn.workers.UvicornWorker` for this app — it adds process-manager complexity without benefit and reintroduces the multi-worker state-sharing problem.

---

## Updated `pyproject.toml` Dependencies

```toml
dependencies = [
    "deepgram-sdk==6.0.1",
    "fastapi>=0.115.0,<1",
    "python-socketio>=5.11.0,<6",
    "uvicorn>=0.30.0,<1",
    "httpx>=0.27.0,<1",
    "python-dotenv==1.0.0",
    "pydub>=0.25.1,<0.26",
    "sounddevice>=0.5.2,<0.6",
]
```

Remove: `flask`, `flask-socketio`, `websocket-client`, `gevent`, `gevent-websocket`, `gunicorn`, `requests`.

---

## Alternatives Considered

| Category | Chosen | Rejected | Why Rejected |
|----------|--------|----------|--------------|
| SocketIO server | `python-socketio` (AsyncServer) | `flask-socketio` | Flask-SocketIO's async mode is gevent-based; no ASGI support |
| SocketIO server | `python-socketio` (AsyncServer) | `fastapi-sio` (wrapper) | Thin wrapper adds abstraction without benefit; direct python-socketio is the official reference |
| HTTP requests | `httpx` | keep `requests` | `requests` is sync-only; in asyncio context needs `run_in_executor` workaround; httpx is async-native and already a transitive dep |
| ASGI server | `uvicorn` | `hypercorn` | uvicorn is the FastAPI standard; hypercorn has no advantages here |
| Deepgram API mode | `async with client.listen.v1.connect()` | `asyncwebsocket.v("1")` (older SDK pattern) | SDK 6.x uses the context-manager API; `asyncwebsocket` path is from SDK 3.x docs |
| Background task | `asyncio.create_task()` | `threading.Thread` | Threading blocks the event loop in asyncio context |

---

## Sources

- deepgram-sdk 6.0.1 source (directly inspected): `/coding/deepgram-python-stt/.venv/lib/python3.12/site-packages/deepgram/` — HIGH confidence
- python-socketio official docs: [python-socketio.readthedocs.io/en/latest/server.html](https://python-socketio.readthedocs.io/en/latest/server.html) — HIGH confidence
- python-socketio GitHub example: [miguelgrinberg/python-socketio — fastapi-fiddle.py](https://github.com/miguelgrinberg/python-socketio/blob/main/examples/server/asgi/fastapi-fiddle.py) — HIGH confidence
- python-socketio latest version 5.16.0 (Dec 2025): [libraries.io/pypi/python-socketio](https://libraries.io/pypi/python-socketio) — HIGH confidence
- FastAPI latest version 0.135.1: [pypi.org/project/fastapi](https://pypi.org/project/fastapi/) — HIGH confidence (web search verified)
- uvicorn latest version 0.41.0: [pypi.org/project/uvicorn](https://pypi.org/project/uvicorn/) — HIGH confidence (web search verified)
- Threading/asyncio gotcha (blocking threading.Event): [python-socketio Discussion #1093](https://github.com/miguelgrinberg/python-socketio/discussions/1093) — MEDIUM confidence
