# Domain Pitfalls: Flask/gevent to FastAPI/asyncio/deepgram-sdk Migration

**Domain:** Browser-based live STT app — Flask+gevent+websocket-client → FastAPI+asyncio+deepgram-sdk+python-socketio
**Researched:** 2026-03-05
**Scope:** Pitfalls specific to THIS migration path, not generic async advice

---

## Critical Pitfalls

Mistakes that cause rewrites, silent failures, or broken tests.

---

### Pitfall 1: python-socketio AsyncServer Has No Built-in test_client

**What goes wrong:** The current test suite uses `socketio.test_client(app)` — a Flask-SocketIO convenience provided only by the synchronous server. `python-socketio`'s `AsyncServer` has no equivalent. If you write tests expecting `socketio.test_client()` to exist on the new server, the entire test file fails to set up.

**Why it happens:** Flask-SocketIO ships a `SocketIOTestClient` that monkey-patches everything in-process. `python-socketio` explicitly decided not to provide this: the recommended testing approach is a real `AsyncClient` connecting to a live Uvicorn server (started inside the test process via `uvicorn.Server`).

**Consequences:** Every existing SocketIO test (`test_socketio_connect`, `test_toggle_transcription_start_emits_stream_started`, `test_detect_audio_settings_emits_response`, etc.) must be rewritten from scratch, not just adapted. Using `socketio.test_client(app)` will raise `AttributeError` or return a sync client that silently never delivers async events.

**Prevention:**
- Design test infrastructure early: a `UvicornTestServer` fixture that starts/stops the ASGI app in-process, plus `socketio.AsyncClient()` fixtures that connect to it.
- Use `pytest-asyncio` with `asyncio_mode = "auto"` in `pyproject.toml` to avoid per-test `@pytest.mark.asyncio` decoration.
- All socket test fixtures must be `async def` and `await client.connect(...)` / `await client.disconnect()`.

**Detection:** `AttributeError: 'AsyncServer' object has no attribute 'test_client'` at fixture setup time.

**Phase:** Address in the test migration phase before touching app logic.

---

### Pitfall 2: Mixing threading Primitives with asyncio Blocks the Entire Event Loop

**What goes wrong:** The current app uses `threading.Event`, `threading.Thread`, and `time.sleep()` inside gevent-patched handlers. Carrying any of these into asyncio handlers on the new stack will block the entire uvicorn event loop — silently, with no exception. `sio.emit()` calls that happen while the loop is blocked will never deliver to clients.

**Why it happens:** `threading.Event.wait()` and `time.sleep()` are blocking calls. Under gevent they are cooperative (monkey-patched). Under native asyncio they block the OS thread the event loop runs on, starving all other coroutines. The python-socketio maintainer explicitly documented this: "the sleep gives you the impression that it addresses the problem... but you are still blocking the loop right after."

**Consequences:**
- Deepgram transcript callbacks that call `await sio.emit(...)` will appear to complete but clients never receive the event.
- `audio_stream` events pile up unprocessed while a blocking operation is in progress.
- File streaming via `time.sleep(0.02)` pacing (currently in `on_start_file_streaming`) will hard-block the loop for every chunk.

**Specific instances in the current codebase:**
- `threading.Thread(target=stream_worker)` in `on_start_file_streaming` — must become an `asyncio.Task`.
- `threading.Event` `stop_flag` — must become `asyncio.Event`.
- `time.sleep(0.02)` pacing in `stream_worker` — must become `await asyncio.sleep(0.02)`.
- `sess["streaming_thread"].is_alive()` check in `on_disconnect` — must be adapted to task cancellation.

**Prevention:**
- Replace all `threading.Event` with `asyncio.Event`.
- Replace all `threading.Thread` with `asyncio.create_task`.
- Replace all `time.sleep(N)` with `await asyncio.sleep(N)`.
- Replace `requests` in `transcribe_batch` (if called from async context) with `httpx.AsyncClient`.
- Audit every handler for any import of `threading` or `time`.

**Detection:** Clients stop receiving SocketIO events intermittently. Event loop appears alive (no crash) but emits are silently dropped.

---

### Pitfall 3: Deepgram SDK AsyncLiveClient Callbacks Must Be async with Correct Signature

**What goes wrong:** The SDK's `AsyncLiveClient` requires all registered callbacks to be `async def` with `(self, result, **kwargs)` signature. If callbacks are sync functions, or if `**kwargs` is omitted, the callbacks are silently never called — transcription updates never arrive, no exception is raised.

**Why it happens:** The SDK awaits callbacks internally. A sync function passed where a coroutine is expected returns a coroutine object that is never awaited. The `**kwargs` requirement exists because the SDK passes additional named arguments (e.g., `**{'client': ...}`) that must be accepted.

**Consequences:** `on_transcript` callbacks never fire. The SocketIO client never receives `transcription_update` events. No error is logged — the stream appears open and sending, but results never come back.

**Correct pattern:**
```python
async def on_transcript(self, result, **kwargs):
    # result is a LiveResultResponse object, not a plain dict
    alt = result.channel.alternatives[0]
    await sio.emit("transcription_update", {...}, room=sid)
```

**Prevention:**
- All Deepgram SDK callbacks must be `async def`.
- All callbacks must accept `**kwargs` as the final parameter.
- The `result` object is a typed `LiveResultResponse` (not a plain dict as in the current websocket-client path) — use `.channel.alternatives[0].transcript`, not `result.get("channel", ...)`.

**Detection:** Stream opens (SDK logs WebSocket connected), audio sends successfully, but `transcription_update` SocketIO events never appear on the client. No exceptions logged.

**Phase:** First thing to validate in the SDK integration phase with a standalone script before wiring SocketIO.

---

### Pitfall 4: Emitting SocketIO Events from Inside AsyncLiveClient Callbacks Requires the Correct Loop Context

**What goes wrong:** The Deepgram `AsyncLiveClient` runs its receive loop as an `asyncio.Task` on the same event loop as the uvicorn server. Calling `await sio.emit(...)` from inside a Deepgram callback should work — but only if the callback is correctly async (Pitfall 3) and only if the session's `sid` is captured at connection-open time, not looked up via `request.sid` (which is only valid during the synchronous request handler).

**Why it happens:** In Flask-SocketIO, `request.sid` is available inside any handler because Flask's request context is thread-local and gevent-patched. In python-socketio async mode, `request.sid` is only available inside the event handler coroutine that originally fired. By the time a Deepgram callback fires (potentially seconds later), there is no Flask request context — `request.sid` raises `RuntimeError`.

**Consequences:** `transcription_update` emits fail with `RuntimeError: Working outside of request context` (or the sid comes back as `None`/wrong session), routing transcripts to the wrong client or dropping them entirely.

**Prevention:**
- Capture `sid` as a local variable inside the SocketIO event handler at the moment the handler fires: `sid = sid` (passed as argument in async mode) or captured in closure.
- Store `sid` in the session dict, never re-derive it from `request.sid` inside callbacks.
- In python-socketio async mode, event handlers receive `sid` as the first argument: `async def on_toggle_transcription(sid, data): ...`

**Detection:** Transcripts route to wrong client, or `RuntimeError` during emit inside Deepgram callback.

---

### Pitfall 5: Premature Connection Close Before Deepgram Finishes Sending Results

**What goes wrong:** Calling `await dg_connection.finish()` immediately after the last audio chunk is sent causes the connection to close before Deepgram has returned final transcripts. The last 1-3 seconds of audio produce no final results.

**Why it happens:** Deepgram processes audio asynchronously on the server side. After you stop sending audio, Deepgram continues processing the buffered audio for several hundred milliseconds to seconds. Closing the connection terminates this processing.

**Consequences:** Audio near the end of a recording is transcribed as interim results only, then lost when the connection closes. This is particularly visible in file streaming, where the end of the file consistently drops final results.

**Prevention:**
- Send `{"type": "CloseStream"}` control message and then wait for Deepgram to send a `SpeechFinal` or `UtteranceEnd` event before calling `finish()`.
- Alternatively: after sending the last chunk, `await asyncio.sleep(1.5)` before `finish()` (coarse but effective).
- Register an `on(LiveTranscriptionEvents.SpeechFinal, ...)` handler and only call `finish()` from there.

**Detection:** Last few words of each utterance disappear in final results but appear briefly as interim results.

**Phase:** Implement correct close sequence in file streaming phase; also affects mic stop.

---

## Moderate Pitfalls

---

### Pitfall 6: gevent monkey.patch_all() Must Be Completely Removed — Not Just Disabled

**What goes wrong:** If any import path or test infrastructure still loads `gevent.monkey.patch_all()` while running under uvicorn/asyncio, it will corrupt the asyncio event loop. The error is `"You cannot use AsyncToSync in the same thread as an async event loop"` from asgiref, or random deadlocks during startup.

**Why it happens:** gevent replaces the standard library socket, threading, and ssl modules globally at import time. asyncio relies on the unpatched versions. When both run in the same process, asyncio's event loop sees gevent-patched sockets and breaks.

**Specific risk:** The current `app.py` starts with `from gevent import monkey; monkey.patch_all()` as lines 1-2. If this file is imported anywhere in the new stack (including during tests that import from `app`), it will fire the patch before uvicorn starts.

**Prevention:**
- Delete the `monkey.patch_all()` calls in the first step of migration.
- Remove `gevent`, `gevent-websocket`, `gunicorn` (gevent worker) from `pyproject.toml` dependencies.
- Replace gunicorn/gevent worker with `uvicorn` as the ASGI server.
- Verify via `python -c "import asyncio; asyncio.run(asyncio.sleep(0))"` works cleanly after removing gevent.

**Detection:** Startup hangs, `RuntimeError` from asgiref, or asyncio event loop not functioning on first request.

---

### Pitfall 7: python-socketio async_mode Must Be "asgi" — Not "threading" or "gevent"

**What goes wrong:** Creating `socketio.AsyncServer(async_mode="threading")` or forgetting `async_mode` entirely causes the server to instantiate the wrong backend. Events fire through a thread-based dispatch that conflicts with uvicorn's event loop, producing deadlocks or events that never deliver.

**Why it happens:** python-socketio supports multiple async backends. When migrating from Flask-SocketIO (which used `async_mode="gevent"`), it is easy to copy the wrong mode or omit it.

**Correct configuration:**
```python
sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")
socket_app = socketio.ASGIApp(sio, other_asgi_app=fastapi_app)
```

**Prevention:**
- Use `socketio.AsyncServer`, not `socketio.Server`.
- Set `async_mode="asgi"` explicitly.
- Mount via `socketio.ASGIApp`, not via Flask-SocketIO's `socketio.run(app)`.
- The ASGI app is served by uvicorn directly: `uvicorn main:socket_app`.

**Detection:** Events appear to send without error but are never received by the client. Or: `ValueError: Invalid async_mode`.

---

### Pitfall 8: KeepAlive Not Sent — Deepgram Closes Idle Connections After ~10 Seconds

**What goes wrong:** If the user pauses speaking (or there is audio silence), Deepgram closes the WebSocket with code 1000 after approximately 10 seconds of receiving no audio or keepalive messages. The frontend then tries to send audio to a closed connection, receiving an error or silently dropping data.

**Why it happens:** The Deepgram SDK's `AsyncLiveClient` supports keepalive, but it must be enabled explicitly via `DeepgramClientOptions`. The current raw websocket-client implementation had the same problem — it just happened to be less visible because gevent-patched sockets handle disconnects differently.

**Prevention:**
```python
config = DeepgramClientOptions(options={"keepalive": "true"})
deepgram = DeepgramClient(API_KEY, config)
```
- Or: send `{"type": "KeepAlive"}` JSON messages on a periodic `asyncio.Task` (every 8 seconds) while the connection is open.

**Detection:** Streams work for the first 10 seconds, then silently fail during pauses. Deepgram logs show close code 1000.

---

### Pitfall 9: Per-Session State Dict is Not asyncio-Safe Without Proper Locking

**What goes wrong:** The current `sessions = {}` global dict is accessed from multiple SocketIO event handlers concurrently (under gevent, this is safe because gevent is cooperative and dict operations don't yield). Under asyncio with uvicorn, concurrent `audio_stream` events and `toggle_transcription` events from the same client can interleave at any `await` point, creating race conditions on `sessions[sid]`.

**Why it happens:** asyncio coroutines run cooperatively but can interleave at every `await`. If `on_toggle_transcription` is mid-way through setting `sess["connection"]` and yields at an `await`, a concurrent `audio_stream` handler can read a partially-constructed session state.

**Consequences:** `NoneType` errors when accessing `sess["connection"]`, or audio being sent to a connection being torn down simultaneously.

**Prevention:**
- Use `asyncio.Lock` per session, or use python-socketio's built-in session management (`async with sio.session(sid) as sess:`), which handles locking.
- The simplest approach: use `sio.save_session` / `sio.get_session` instead of a raw global dict.

**Detection:** Intermittent `AttributeError: 'NoneType' object has no attribute 'send'` during rapid start/stop cycles.

---

### Pitfall 10: pytest-asyncio Event Loop Scope Mismatches Cause "Event Loop is Closed" Errors

**What goes wrong:** When using session-scoped fixtures (like a shared Uvicorn server) with function-scoped async tests, pytest-asyncio creates a new event loop per test function. The server fixture was started on a different loop, so all async operations on it raise `RuntimeError: Event loop is closed`.

**Why it happens:** pytest-asyncio's default `event_loop` fixture is function-scoped. Session-scoped async fixtures require an explicitly session-scoped event loop.

**Prevention:**
```python
# conftest.py
import pytest
import pytest_asyncio

@pytest.fixture(scope="session")
def event_loop_policy():
    return asyncio.DefaultEventLoopPolicy()

# In pyproject.toml:
# [tool.pytest.ini_options]
# asyncio_mode = "auto"
```
Or use `pytest-anyio` which handles scope more gracefully.

- The Uvicorn test server fixture must have the same scope as the event loop.
- `AsyncClient` connect/disconnect must happen within the same event loop context.

**Detection:** `RuntimeError: Event loop is closed` on second or later socket test. First test passes, subsequent tests fail.

**Phase:** Establish the test infrastructure fixture design early and validate with a single connection test before writing all event tests.

---

## Minor Pitfalls

---

### Pitfall 11: deepgram-sdk LiveResultResponse Object vs. Plain Dict

**What goes wrong:** The current `on_transcript` callback receives a plain Python dict (because websocket-client returns raw JSON). The SDK's `AsyncLiveClient` delivers a typed `LiveResultResponse` object. Code like `result.get("channel", {})` raises `AttributeError: 'LiveResultResponse' object has no attribute 'get'`.

**Prevention:**
- Use `result.channel.alternatives[0].transcript` (attribute access, not dict access).
- Use `result.model_dump()` if you need to serialize the whole response to JSON for the frontend.

---

### Pitfall 12: SocketIO ASGI Mounting Order Determines Which Routes Are Reachable

**What goes wrong:** If FastAPI is mounted as the `other_asgi_app` inside `socketio.ASGIApp`, but the Socket.IO path conflicts with a FastAPI route, FastAPI routes become unreachable or 404.

**Prevention:**
- Mount Socket.IO at a subpath if needed: `socketio.ASGIApp(sio, other_asgi_app=fastapi_app, socketio_path="/socket.io")`.
- Verify HTTP routes (`/`, `/upload`, `/transcribe`) still return 200 after ASGI wiring.
- Test HTTP and SocketIO routes separately in integration tests.

---

### Pitfall 13: AsyncClient in Tests Hangs on Disconnect Without Proper Await

**What goes wrong:** `await sio_client.disconnect()` in test teardown hangs indefinitely if the server-side `on_disconnect` handler raises an exception or takes too long. The test process then hangs after all tests complete.

**Prevention:**
- Use `await asyncio.wait_for(sio_client.disconnect(), timeout=5)` in teardown.
- Ensure the server-side `on_disconnect` handler has a try/except and cleans up without blocking.

---

### Pitfall 14: FastAPI HTTP Routes Must Use async def to Avoid Blocking the Event Loop

**What goes wrong:** Migrating Flask routes (which are sync `def`) to FastAPI sync `def` routes is valid — FastAPI runs sync routes in a threadpool. But if those sync routes call `requests.post(...)` for batch transcription (the current `transcribe_batch` implementation), the threadpool thread blocks for the full HTTP round-trip. Under high load this exhausts the threadpool.

**Prevention:**
- Convert `/transcribe` route to `async def` and replace `requests` with `httpx.AsyncClient`.
- Or: keep sync `def` routes but explicitly document the threadpool dependency.

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|---|---|---|
| Remove gevent, add FastAPI/uvicorn | gevent monkey-patch in app.py fires before asyncio starts | Delete monkey.patch_all() as first edit, verify asyncio works cleanly |
| python-socketio AsyncServer wiring | Wrong async_mode ("threading" instead of "asgi") | Use `socketio.AsyncServer(async_mode="asgi")` explicitly; test with a connect event immediately |
| Per-session state management | Global dict race conditions at await points | Switch to `sio.session(sid)` context manager from day one; don't port the raw dict |
| Deepgram SDK AsyncLiveClient integration | Callbacks never called (sync def, missing **kwargs) | Validate callback receipt with a standalone asyncio script before integrating SocketIO |
| Deepgram SDK AsyncLiveClient integration | result is LiveResultResponse, not dict | Audit all result.get() calls; replace with attribute access |
| Deepgram SDK connection close | Final words dropped (connection closed before results arrive) | Implement CloseStream + SpeechFinal/UtteranceEnd-gated finish() |
| File streaming migration | time.sleep() blocks loop; threading.Event blocks loop | Replace with asyncio.sleep() and asyncio.Event before any other async logic |
| Test migration: SocketIO events | No test_client on AsyncServer | Write UvicornTestServer + AsyncClient fixture first; validate one connect test |
| Test migration: pytest-asyncio | Event loop scope mismatch causes test-2+ failures | Use session-scoped event loop fixture; validate with 3+ sequential socket tests |
| All SocketIO handlers | request.sid not available inside Deepgram callbacks | Capture sid as local variable in handler closure at event-fire time |

---

## Sources

- [python-socketio: FastAPI emit not delivered without asyncio.sleep](https://github.com/miguelgrinberg/python-socketio/discussions/1093) — confirms threading.Event blocks asyncio loop
- [python-socketio: Is there a test client?](https://github.com/miguelgrinberg/python-socketio/issues/332) — confirms no built-in AsyncServer test_client
- [deepgram-sdk: Async live client on_message not called](https://github.com/deepgram/deepgram-python-sdk/issues/442) — confirms callback signature requirement (self, result, **kwargs)
- [deepgram-sdk: Issues running with FastAPI sockets](https://github.com/deepgram/deepgram-python-sdk/issues/361) — confirms keepalive requirement, audio encoding match
- [deepgram-sdk: WebSocket issue in 3.1.1](https://github.com/deepgram/deepgram-python-sdk/issues/279) — version-specific connection failures
- [pytest-asyncio: Async fixtures may break current event loop](https://github.com/pytest-dev/pytest-asyncio/issues/868) — event loop scope issues
- [pytest-asyncio: AsyncClient in tests hangs](https://github.com/miguelgrinberg/python-socketio/issues/263) — disconnect hang pattern
- [gevent + asgiref conflict (Flask issue)](https://github.com/pallets/flask/issues/5881) — confirms monkey.patch_all() breaks asyncio
- [python-socketio server documentation](https://python-socketio.readthedocs.io/en/latest/server.html) — async_mode="asgi" canonical pattern
- [Deepgram live transcription with FastAPI blog](https://deepgram.com/learn/live-transcription-fastapi) — official FastAPI integration pattern
