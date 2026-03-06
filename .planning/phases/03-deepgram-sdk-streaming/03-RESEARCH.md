# Phase 3: Deepgram SDK Streaming - Research

**Researched:** 2026-03-05
**Domain:** deepgram-sdk 6.x async streaming, asyncio task management, python-socketio per-session state
**Confidence:** HIGH (all findings from installed SDK source, live package inspection)

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| STR-01 | Live mic streaming uses `AsyncDeepgramClient.listen.v1.connect()` (deepgram-sdk 6.x async API) | SDK source confirmed: `AsyncV1Client.connect()` is an `@asynccontextmanager` yielding `AsyncV1SocketClient` |
| STR-02 | Per-session Deepgram connection runs as an `asyncio.Task`, replacing `threading.Thread` | Pattern: wrap the `async with dg.listen.v1.connect() as ws: await ws.start_listening()` block in `asyncio.create_task()` |
| STR-03 | `send_keep_alive()` sent periodically to prevent Deepgram idle timeout during speech pauses | SDK confirmed: `AsyncV1SocketClient.send_keep_alive()` sends `{"type": "KeepAlive"}` — wrap in a periodic asyncio loop |
| STR-04 | Stream stop waits for final results before closing — no dropped final words | SDK pattern: `send_close_stream()` triggers Deepgram to flush + send final `is_final=True` result, then close; must await that final message before tearing down |
</phase_requirements>

---

## Summary

Phase 3 replaces `stt/client.py` (which uses `websocket-client` + `threading.Thread`) with the deepgram-sdk 6.x async API. The SDK is already installed at version 6.0.1 in the project virtualenv. The core type is `AsyncDeepgramClient` (from `deepgram`), whose `listen.v1.connect()` method is an `@asynccontextmanager` that yields `AsyncV1SocketClient`. This client has three critical async methods: `send_media(bytes)`, `send_keep_alive()`, and `send_close_stream()`. Transcription results arrive via `start_listening()` which emits `EventType.MESSAGE` events carrying `ListenV1Results` Pydantic objects.

The primary implementation challenge is the concurrent-task structure: each SocketIO session needs its own `asyncio.Task` that owns the Deepgram WebSocket lifecycle (open, stream, keep-alive, close), while the `on_audio_stream` handler forwards audio chunks into it. Per-session state (the task handle, a stop-flag asyncio.Event, and the request_id from Metadata) must be stored somewhere retrievable by both `on_audio_stream` and `on_toggle_transcription(action=stop)`. The `sio.session(sid)` context manager is the correct python-socketio mechanism for this; it is an async context manager that persists a dict to the socket session.

**Primary recommendation:** Use a module-level `dict[str, dict]` (keyed by sid) for per-session state rather than `sio.session()`, because session state requires two async roundtrips on every audio chunk (get + save), making it too slow for the hot path. The `sio.session()` is appropriate only for low-frequency connect/disconnect setup.

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `deepgram` (deepgram-sdk) | 6.0.1 (pinned) | Async Deepgram WebSocket client | Already installed; this is the reference SDK the project is demonstrating |
| `asyncio` (stdlib) | Python 3.12 | Task management, Events, Queues | Already in use throughout the stack |
| `python-socketio` | >=5.11.0 | SocketIO server for browser events | Already in use |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `asyncio.Event` | stdlib | Stop signal for the streaming task | Signal the keep-alive loop and listener to stop gracefully |
| `asyncio.Queue` | stdlib | Audio chunk buffer between handler and streamer | Optional; alternative to direct `await ws.send_media()` calls — use only if backpressure is needed |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Module-level `sessions: dict[str, dict]` | `sio.session(sid)` context manager | `sio.session()` requires two async DB-roundtrip calls per audio chunk (get + save); sessions dict avoids this but requires manual cleanup on disconnect |
| `asyncio.Task` per session | `asyncio.create_task()` then track task in sessions dict | Same thing — just spelling out where the task lives |
| `ws.start_listening()` with `.on()` callbacks | `async for msg in ws:` iteration | Both work; `start_listening()` with event callbacks matches the SDK's own docs and is more idiomatic |

**No new packages required.** deepgram-sdk 6.0.1 is already in `pyproject.toml`.

---

## Architecture Patterns

### Recommended Structure

```
app.py                  # All streaming logic lives here (no separate module needed yet)
stt/
  client.py             # OLD — keep for reference, Phase 3 does NOT use this
  options.py            # KEEP — clean_params() is reused in Phase 3 to build SDK kwargs
```

Phase 3 adds streaming logic directly to `app.py`. The `stt/client.py` file is retired but not deleted (that is Phase 5 cleanup).

### Per-Session State Dict Pattern

```python
# Module-level — lives at the top of app.py, alongside sio and fastapi_app
# Key: SocketIO session id (sid)
# Value: dict with keys: task, stop_event, request_id
_sessions: dict[str, dict] = {}
```

Access pattern:
- `on_toggle_transcription(action=start)` → creates entry, spawns task
- `on_audio_stream(sid, data)` → looks up entry, calls `await task_state["ws"].send_media(data)`
- `on_toggle_transcription(action=stop)` → sets `stop_event`, awaits task
- `on_disconnect(sid)` → cleans up entry if present

### Pattern 1: Streaming Task Skeleton

**What:** A single coroutine that owns the entire Deepgram WebSocket lifecycle for one session.
**When to use:** Always — one task per active session, started at `action=start`, cancelled/stopped at `action=stop`.

```python
# Source: deepgram-sdk 6.0.1 installed source
# /coding/deepgram-python-stt/.venv/lib/python3.12/site-packages/deepgram/listen/v1/client.py
# AsyncV1Client.connect() is an @asynccontextmanager

async def streaming_task(sid: str, params: dict, stop_event: asyncio.Event) -> None:
    """Owns the Deepgram WebSocket for one session. Runs as an asyncio.Task."""
    from deepgram import AsyncDeepgramClient
    api_key = os.getenv("DEEPGRAM_API_KEY", "")
    dg = AsyncDeepgramClient(api_key)

    # Build SDK kwargs from the clean_params() output
    # (reuse stt/options.py clean_params — it still works for filtering)
    sdk_kwargs = _params_to_sdk_kwargs(params)

    async with dg.listen.v1.connect(**sdk_kwargs) as ws:
        # Store ws reference so audio chunks can call ws.send_media()
        _sessions[sid]["ws"] = ws

        # Capture request_id from Metadata (arrives first, before any Results)
        request_id_event = asyncio.Event()

        async def on_message(msg):
            if isinstance(msg, ListenV1Metadata):
                _sessions[sid]["request_id"] = msg.request_id
                request_id_event.set()
            elif isinstance(msg, ListenV1Results):
                transcript = msg.channel.alternatives[0].transcript
                is_final = bool(msg.is_final)
                await sio.emit("transcription_update", {
                    "transcript": transcript,
                    "is_final": is_final,
                }, to=sid)

        ws.on(EventType.MESSAGE, on_message)
        listen_task = asyncio.create_task(ws.start_listening())

        # Wait for Metadata to arrive, then emit stream_started
        await asyncio.wait_for(request_id_event.wait(), timeout=10.0)
        await sio.emit("stream_started", {
            "request_id": _sessions[sid].get("request_id"),
        }, to=sid)

        # Keep-alive loop — runs until stop_event is set
        async def keep_alive_loop():
            while not stop_event.is_set():
                await asyncio.sleep(8)  # send every 8s (under 10s timeout)
                if not stop_event.is_set():
                    await ws.send_keep_alive()

        ka_task = asyncio.create_task(keep_alive_loop())

        # Wait for stop signal
        await stop_event.wait()

        # Graceful shutdown: send CloseStream, wait for listen_task to finish
        ka_task.cancel()
        await ws.send_close_stream()
        await listen_task  # blocks until Deepgram sends final Results + closes

    await sio.emit("stream_finished", {
        "request_id": _sessions[sid].get("request_id"),
    }, to=sid)
    _sessions.pop(sid, None)
```

### Pattern 2: Toggle Handler

```python
@sio.on("toggle_transcription")
async def on_toggle_transcription(sid, data):
    action = data.get("action", "start")
    params = data.get("params", {})

    if action == "start":
        if sid in _sessions:
            # Already streaming — idempotent: ignore or stop first
            return
        stop_event = asyncio.Event()
        _sessions[sid] = {"stop_event": stop_event, "ws": None, "request_id": None}
        task = asyncio.create_task(streaming_task(sid, params, stop_event))
        _sessions[sid]["task"] = task

    elif action == "stop":
        if sid not in _sessions:
            # Not streaming — emit stream_finished to keep frontend in sync
            await sio.emit("stream_finished", {"request_id": None}, to=sid)
            return
        _sessions[sid]["stop_event"].set()
        # stream_finished emitted by streaming_task after it finishes
```

### Pattern 3: Audio Chunk Handler

```python
@sio.on("audio_stream")
async def on_audio_stream(sid, data):
    session = _sessions.get(sid)
    if session and session.get("ws"):
        # data is bytes from the browser
        await session["ws"].send_media(data if isinstance(data, bytes) else bytes(data))
```

### Pattern 4: Disconnect Cleanup

```python
@sio.event
async def disconnect(sid, reason=None):
    session = _sessions.pop(sid, None)
    if session:
        session["stop_event"].set()
        task = session.get("task")
        if task and not task.done():
            task.cancel()
    logger.info("Client disconnected: %s reason=%s", sid, reason)
```

### How to bridge stt/options.py clean_params() to SDK kwargs

The SDK `AsyncV1Client.connect()` accepts individual keyword arguments (not a dict). The existing `clean_params()` returns a `dict`. Map them:

```python
from stt.options import clean_params, Mode

def _params_to_sdk_kwargs(raw_params: dict) -> dict:
    """Convert frontend params dict to deepgram-sdk 6.x keyword args."""
    clean = clean_params(raw_params, Mode.STREAMING)
    # SDK expects string values for most params, and model is required
    return {k: str(v) if not isinstance(v, (list, str)) else v
            for k, v in clean.items()}
```

Note: `model` is the only **required** keyword argument to `connect()`. If `raw_params` does not include it, provide a sensible default (e.g., `"nova-2"`).

### Anti-Patterns to Avoid

- **Storing `ws` in `sio.session()`**: Requires `async with sio.session(sid) as s:` context manager on every audio chunk — two async roundtrips per chunk. Use module-level dict instead.
- **Using `threading.Thread` or `time.sleep` in any streaming path**: Blocks the asyncio event loop. All sleeps must be `await asyncio.sleep()`.
- **Calling `listen_task.cancel()` directly for stop**: This forcibly kills the receive loop before Deepgram sends final results. Always send `send_close_stream()` first, then await the listen task.
- **Not cleaning up `_sessions` on disconnect**: Leaks task and WebSocket handles. Always clean up in `on_disconnect`.
- **Awaiting `stop_event.wait()` without also awaiting the Deepgram close**: The streaming task must block until `listen_task` completes (Deepgram closes the WebSocket), not just until the stop_event fires.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| WebSocket keep-alive message format | Custom JSON `{"type": "KeepAlive"}` | `ws.send_keep_alive()` | SDK method is typed and idempotent; `ListenV1KeepAlive` Pydantic model handles serialization |
| CloseStream handshake | Custom JSON `{"type": "CloseStream"}` | `ws.send_close_stream()` | SDK method handles `ListenV1CloseStream` serialization; matches Deepgram protocol exactly |
| Result parsing | Manual `json.loads()` + dict access | `ListenV1Results` Pydantic model | SDK parses in `start_listening()` — results arrive already typed |
| URL construction with query params | Manual URL building | `connect(**sdk_kwargs)` | SDK handles URL encoding, auth headers, query param serialization |

---

## Common Pitfalls

### Pitfall 1: Sending audio before ws is stored
**What goes wrong:** `on_audio_stream` fires immediately after `toggle_transcription(start)` — before the `streaming_task` has opened the WebSocket and assigned `_sessions[sid]["ws"]`.
**Why it happens:** Task scheduling — `asyncio.create_task()` does not run the task synchronously; the first audio chunk can arrive before the WebSocket is open.
**How to avoid:** Check `session.get("ws")` is not None before calling `send_media()`. Drop chunks silently until the WebSocket is ready — the browser buffers more audio anyway.
**Warning signs:** `AttributeError: 'NoneType' has no attribute 'send_media'` in audio_stream handler.

### Pitfall 2: Dropped final words on stop
**What goes wrong:** `stream_finished` emits before Deepgram sends final `is_final=True` results for buffered audio.
**Why it happens:** If `listen_task` is cancelled or `stream_finished` is emitted immediately after `send_close_stream()`, the last Results message never arrives.
**How to avoid:** After `send_close_stream()`, `await listen_task` — this blocks until Deepgram closes its end of the WebSocket, guaranteeing all results have been delivered. Only emit `stream_finished` after `listen_task` completes.
**Warning signs:** User reports last 1-2 words of speech missing from transcript.

### Pitfall 3: Keep-alive interval too long
**What goes wrong:** Deepgram silently disconnects during speech pauses with no error.
**Why it happens:** Deepgram's idle timeout is approximately 10 seconds (documented in STATE.md as estimated). If no audio or KeepAlive arrives in that window, the connection closes.
**How to avoid:** Send `send_keep_alive()` every 8 seconds (under the threshold). Use `asyncio.sleep(8)`, not `time.sleep(8)`.
**Warning signs:** Streaming stops mid-pause without `stream_finished` being emitted; Deepgram WebSocket closes with code 1000 or 1001.

### Pitfall 4: start/stop/start cycle leaks state
**What goes wrong:** Second `toggle_transcription(start)` overwrites `_sessions[sid]` while the old task is still running, creating an orphaned task that holds a WebSocket open.
**Why it happens:** Stop sets the event, but if `toggle_transcription(start)` arrives before the old task finishes tearing down, there are two tasks for the same sid.
**How to avoid:** In `on_toggle_transcription(action=start)`, check `sid in _sessions` — if already present, either return early or await the old task before creating a new one. Simplest: early return with a log warning.
**Warning signs:** Two `stream_started` events emitted, doubled transcription updates.

### Pitfall 5: `EventType.MESSAGE` callback is sync but calls `sio.emit()`
**What goes wrong:** Calling `await sio.emit(...)` inside a sync callback registered with `ws.on(EventType.MESSAGE, cb)` raises `RuntimeWarning: coroutine was never awaited`.
**Why it happens:** The `EventEmitterMixin._emit_async()` does check `isawaitable()` and awaits, but only if the callback itself is `async def`. Sync callbacks that return a coroutine will have it awaited; but a sync callback that tries to `asyncio.create_task()` internally will work fine too.
**How to avoid:** Always define the MESSAGE callback as `async def on_message(msg):` so `_emit_async` properly awaits it.
**Warning signs:** `RuntimeWarning: coroutine 'emit' was never awaited`; transcription_update events never arrive at client.

### Pitfall 6: `model` is required in `connect()`
**What goes wrong:** `connect()` raises `TypeError: connect() missing 1 required keyword-only argument: 'model'`.
**Why it happens:** The SDK signature has `model: str` as required (no default).
**How to avoid:** Ensure `_params_to_sdk_kwargs()` always includes `model`. If frontend params don't include it, default to `"nova-2"`.
**Warning signs:** `TypeError` at task start; `stream_started` never fires.

---

## Code Examples

### Connecting to Deepgram with AsyncDeepgramClient

```python
# Source: deepgram-sdk 6.0.1
# /coding/deepgram-python-stt/.venv/lib/python3.12/site-packages/deepgram/listen/v1/client.py
# AsyncV1Client.connect() — @asynccontextmanager yielding AsyncV1SocketClient

from deepgram import AsyncDeepgramClient

dg = AsyncDeepgramClient(api_key)

async with dg.listen.v1.connect(model="nova-2", encoding="linear16", sample_rate="16000") as ws:
    # ws is AsyncV1SocketClient
    await ws.start_listening()  # blocks until WebSocket closes
```

### Registering event callbacks on AsyncV1SocketClient

```python
# Source: deepgram-sdk 6.0.1
# /coding/deepgram-python-stt/.venv/lib/python3.12/site-packages/deepgram/core/events.py
# EventEmitterMixin — _emit_async() checks isawaitable() and awaits if True

from deepgram.core.events import EventType
from deepgram.listen.v1.types import ListenV1Results, ListenV1Metadata

async def on_message(msg):
    if isinstance(msg, ListenV1Results):
        transcript = msg.channel.alternatives[0].transcript
        is_final = bool(msg.is_final)
        # handle result...
    elif isinstance(msg, ListenV1Metadata):
        request_id = msg.request_id
        # handle metadata...

ws.on(EventType.MESSAGE, on_message)
listen_task = asyncio.create_task(ws.start_listening())
```

### Sending keep-alive

```python
# Source: deepgram-sdk 6.0.1
# /coding/deepgram-python-stt/.venv/lib/python3.12/site-packages/deepgram/listen/v1/socket_client.py
# Sends {"type": "KeepAlive"} as ListenV1KeepAlive

await ws.send_keep_alive()  # no argument needed — default is ListenV1KeepAlive(type="KeepAlive")
```

### Graceful stream close (preserves final results)

```python
# Source: deepgram-sdk 6.0.1
# send_close_stream sends {"type": "CloseStream"}
# Deepgram then flushes, sends final Results, closes WebSocket
# listen_task then exits naturally

await ws.send_close_stream()
await listen_task  # MUST await — ensures final Results are received before stream_finished
```

### ListenV1Results data access

```python
# Source: deepgram-sdk 6.0.1
# /coding/deepgram-python-stt/.venv/lib/python3.12/site-packages/deepgram/listen/v1/types/
# ListenV1Results -> .channel -> .alternatives[0] -> .transcript / .confidence

result: ListenV1Results  # arrives via EventType.MESSAGE

transcript = result.channel.alternatives[0].transcript  # str
is_final = result.is_final  # Optional[bool]
speech_final = result.speech_final  # Optional[bool]
```

### Imports required in app.py

```python
from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType
from deepgram.listen.v1.types import (
    ListenV1Results,
    ListenV1Metadata,
)
from stt.options import clean_params, Mode
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `websocket.create_connection()` + `threading.Thread` | `AsyncDeepgramClient.listen.v1.connect()` + `asyncio.Task` | Phase 3 (now) | Fully asyncio-native, no GIL contention |
| `json.loads()` + manual dict traversal | `ListenV1Results` Pydantic model | Phase 3 (now) | Typed access; SDK parses in start_listening() |
| Custom `send(json.dumps({"type": "CloseStream"}))` | `ws.send_close_stream()` | Phase 3 (now) | Protocol-correct, no manual JSON |
| `requests` library for batch | `httpx.AsyncClient` (Phase 4) | Phase 4 (later) | Not in scope for Phase 3 |

**Deprecated/outdated in stt/client.py:**
- `STTClient.open_stream()`: Replaced by `AsyncDeepgramClient.listen.v1.connect()`
- `STTClient.send_media()`: Replaced by `ws.send_media(bytes)`
- `STTClient.send_close_stream()`: Replaced by `ws.send_close_stream()`
- All `threading.Thread`, `threading.Event`, `time.sleep` usage

---

## Open Questions

1. **Deepgram idle timeout exact value**
   - What we know: STATE.md notes "10-second idle timeout is estimated, not formally documented"
   - What's unclear: Is it 10s, 12s, or configurable? Does KeepAlive reset the clock?
   - Recommendation: Use 8-second keep-alive interval as a conservative default. Validate during integration testing by introducing a 15-second silence and verifying stream does not drop.

2. **`request_id` timing relative to `stream_started` emission**
   - What we know: `ListenV1Metadata` arrives before `ListenV1Results` (it's the first message Deepgram sends on connect). STATE.md notes this as needing validation.
   - What's unclear: Exact latency between WebSocket open and Metadata arrival — could be 50ms or 500ms.
   - Recommendation: Use an `asyncio.Event` (as shown in the Pattern 1 skeleton) to wait for Metadata before emitting `stream_started`. Apply a 10-second timeout to avoid hanging forever on API errors.

3. **Audio chunk format from browser**
   - What we know: Frontend sends binary audio chunks via `audio_stream` SocketIO event. The `on_audio_stream` handler receives `data`.
   - What's unclear: Whether `data` arrives as `bytes` or `bytearray` or `list[int]` depending on SocketIO serialization.
   - Recommendation: Cast defensively: `bytes(data) if not isinstance(data, bytes) else data` before calling `send_media()`.

---

## Validation Architecture

> `workflow.nyquist_validation` is not set in `.planning/config.json` — this section is included as a guide for test design only.

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest + pytest-asyncio 0.23.x |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` |
| Quick run command | `uv run pytest tests/test_app.py -x -q` |
| Full suite command | `uv run pytest tests/ -v` |

### Phase Requirements to Test Map

| Req ID | Behavior | Test Type | Automated Command |
|--------|----------|-----------|-------------------|
| STR-01 | `toggle_transcription(start)` emits `stream_started` with non-None `request_id` (requires real Deepgram key) | integration (manual/skipped in CI) | Manual only — needs live API key |
| STR-01 | Deepgram connection uses `AsyncDeepgramClient`, not `websocket-client` | unit (grep/import check) | `uv run pytest tests/test_app.py::test_no_websocket_client_import -x` |
| STR-02 | `_sessions[sid]["task"]` is an `asyncio.Task` when streaming is active | unit (mock Deepgram) | `uv run pytest tests/test_streaming.py -x` |
| STR-03 | Keep-alive is sent after 8s of inactivity | unit (mock ws.send_keep_alive) | `uv run pytest tests/test_streaming.py::test_keep_alive -x` |
| STR-04 | `stream_finished` only emits after all `is_final=True` results processed | unit (mock) | `uv run pytest tests/test_streaming.py::test_graceful_stop -x` |

### Wave 0 Gaps
- [ ] `tests/test_streaming.py` — new file for Phase 3 unit tests (mock Deepgram WebSocket)
- [ ] Mock strategy: create a fake `AsyncV1SocketClient` that yields controlled `ListenV1Results` objects

---

## Sources

### Primary (HIGH confidence)
- Installed SDK source: `/coding/deepgram-python-stt/.venv/lib/python3.12/site-packages/deepgram/` — full source inspection
  - `listen/v1/client.py` — `AsyncV1Client.connect()` signature and implementation
  - `listen/v1/socket_client.py` — `AsyncV1SocketClient` methods: `send_media`, `send_keep_alive`, `send_close_stream`, `start_listening`
  - `listen/v1/types/listen_v1results.py` — `ListenV1Results` Pydantic model fields
  - `listen/v1/types/listen_v1metadata.py` — `ListenV1Metadata.request_id` field
  - `listen/v1/types/listen_v1results_channel.py` — `ListenV1ResultsChannel.alternatives`
  - `listen/v1/types/listen_v1results_channel_alternatives_item.py` — `.transcript`, `.confidence`
  - `core/events.py` — `EventEmitterMixin`, `EventType`, `_emit_async()` awaitable check
  - `client.py` — `AsyncDeepgramClient` custom constructor
- python-socketio source: `/coding/deepgram-python-stt/.venv/lib/python3.12/site-packages/socketio/async_server.py`
  - `get_session()`, `save_session()`, `session()` context manager — confirmed API

### Secondary (MEDIUM confidence)
- `STATE.md` accumulated decisions — prior researcher findings about SDK callback requirements (`async def` with `**kwargs`), Deepgram 10s idle timeout (estimated)
- `stt/client.py` — existing implementation to understand what is being replaced

### Tertiary (LOW confidence)
- Deepgram 10-second idle timeout — estimated from prior work, not verified against official documentation; must be validated during integration testing

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — SDK is installed; source is directly inspected
- Architecture patterns: HIGH — patterns derived from SDK source, not external docs
- Pitfalls: HIGH (pitfalls 1-4) / MEDIUM (pitfall 3 keep-alive interval, which depends on unverified 10s timeout)
- Per-session state approach: HIGH — based on direct socketio source inspection

**Research date:** 2026-03-05
**Valid until:** 2026-09-05 (deepgram-sdk 6.0.1 is pinned; patterns stable until SDK major version bump)
