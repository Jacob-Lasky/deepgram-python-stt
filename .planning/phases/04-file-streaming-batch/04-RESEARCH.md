# Phase 4: File Streaming + Batch - Research

**Researched:** 2026-03-05
**Domain:** Deepgram SDK 6.x async file streaming, httpx AsyncClient for batch transcription
**Confidence:** HIGH

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| FILE-01 | File upload streaming reuses the same async streaming infrastructure as mic streaming | streaming_task() already exists and accepts any audio source via ws.send_media(); file bytes can be chunked and pumped through the same WebSocket path |
| FILE-02 | Batch `/transcribe` route uses `httpx.AsyncClient` instead of `requests` | httpx is already in pyproject.toml; the `/transcribe` stub exists and needs a real implementation using httpx directly (not via the SDK's MediaClient, which uses its own internal httpx wrapper) |
</phase_requirements>

---

## Summary

Phase 4 extends the async infrastructure built in Phase 3 to two remaining features: file-based streaming transcription and batch (REST) transcription. Both currently have stub handlers in `app.py`.

For **FILE-01 (file streaming)**, the key insight is that `streaming_task()` already handles the full Deepgram WebSocket lifecycle. File streaming only requires a new task entry point that opens a file from `TEMP_DIR`, reads it in chunks, and calls `ws.send_media()` in a loop — identical to what the mic does via `on_audio_stream`. The `stop_event` mechanism already handles graceful teardown with `send_close_stream()` and `listen_task` flush. File streaming must set `stop_event` automatically when EOF is reached (not wait for the client to call `stop_file_streaming`).

For **FILE-02 (batch)**, the `/transcribe` stub must be replaced with a real implementation using `httpx.AsyncClient` directly against the Deepgram REST API (`https://api.deepgram.com/v1/listen`). The frontend sends a JSON body with either `url` (remote audio URL) or `filename` (uploaded file in TEMP_DIR) plus `params`. The SDK's `AsyncMediaClient.transcribe_url()` and `transcribe_file()` are available but use the SDK's internal httpx wrapper — it is simpler and more transparent to use `httpx.AsyncClient` directly, which is what the requirement specifies and aligns with the constraint that `requests` must not be used.

**Primary recommendation:** Implement `file_streaming_task()` that reuses `streaming_task()` infrastructure by sharing the same session dict shape and `_params_to_sdk_kwargs()` helper; implement the batch `/transcribe` route with `httpx.AsyncClient` calling `https://api.deepgram.com/v1/listen` with query params and either a JSON body `{"url": "..."}` or a binary body from file.

---

## Standard Stack

### Core (already in pyproject.toml)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| deepgram-sdk | 6.0.1 | `AsyncDeepgramClient.listen.v1.connect()` for file streaming WS | Already proven in Phase 3 |
| httpx | >=0.27.0,<1 | `httpx.AsyncClient` for batch REST calls | In pyproject.toml; asyncio-native |
| fastapi | >=0.115.0,<1 | Route handlers for `/transcribe` | Already serving the app |
| python-socketio | >=5.11.0,<6 | `start_file_streaming` / `stop_file_streaming` SocketIO events | Already established |

### No new installs required
All necessary libraries are already installed. Phase 4 is pure implementation work.

---

## Architecture Patterns

### Recommended Project Structure

No structural changes needed. All changes are within `app.py`:

```
app.py
├── file_streaming_task()     # NEW: reads file, pumps chunks via ws.send_media()
├── on_start_file_streaming() # UPDATE: replace stub with real task spawn
├── on_stop_file_streaming()  # UPDATE: replace stub with stop_event.set()
└── transcribe() (HTTP route) # UPDATE: replace 501 stub with httpx batch call
```

### Pattern 1: File Streaming via Shared WS Infrastructure

**What:** `file_streaming_task()` is a thin wrapper around the same `dg.listen.v1.connect()` pattern as `streaming_task()`. It opens a file from `TEMP_DIR`, reads it in chunks (e.g. 4096 bytes), calls `ws.send_media(chunk)` in a loop, then signals EOF by sending `CloseStream` — without waiting for an external stop signal.

**When to use:** When `start_file_streaming` SocketIO event arrives with `{filename, params}`.

**Key difference from mic streaming:** Mic streaming waits on `stop_event` (user clicks Stop). File streaming self-terminates at EOF — it sends all chunks, calls `ws.send_close_stream()`, then awaits `listen_task` to flush. `stop_event` is still used to allow early cancellation if user clicks Stop.

**Example:**
```python
# Source: deepgram SDK socket_client.py + existing streaming_task() pattern in app.py
async def file_streaming_task(sid: str, filename: str, params: dict, stop_event: asyncio.Event) -> None:
    """Streams an uploaded file through Deepgram WS. Self-terminates at EOF."""
    api_key = os.getenv("DEEPGRAM_API_KEY", "")
    dg = AsyncDeepgramClient(api_key=api_key)
    sdk_kwargs = _params_to_sdk_kwargs(params)
    file_path = TEMP_DIR / filename

    try:
        async with dg.listen.v1.connect(**sdk_kwargs) as ws:
            if sid in _sessions:
                _sessions[sid]["ws"] = ws

            async def on_message(msg, **kwargs):
                if isinstance(msg, ListenV1Results):
                    transcript = msg.channel.alternatives[0].transcript
                    is_final = bool(msg.is_final)
                    await sio.emit("transcription_update", {
                        "transcript": transcript,
                        "is_final": is_final,
                    }, to=sid)

            ws.on(EventType.MESSAGE, on_message)
            listen_task = asyncio.create_task(ws.start_listening())

            await sio.emit("stream_started", {"request_id": None}, to=sid)

            # Pump file chunks — stop early if user cancels
            CHUNK_SIZE = 4096
            with open(file_path, "rb") as f:
                while not stop_event.is_set():
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break  # EOF
                    await ws.send_media(chunk)

            # Send CloseStream to flush final results
            try:
                await ws.send_close_stream()
                await listen_task  # blocks until Deepgram sends all Results
            except (asyncio.CancelledError, Exception) as e:
                logger.warning("[%s] file stream shutdown error: %s", sid, e)
                if not listen_task.done():
                    listen_task.cancel()

    except Exception as e:
        logger.error("[%s] file_streaming_task error: %s", sid, e)
    finally:
        request_id = _sessions[sid].get("request_id") if sid in _sessions else None
        await sio.emit("stream_finished", {"request_id": request_id}, to=sid)
        _sessions.pop(sid, None)
```

### Pattern 2: Batch Transcription via httpx.AsyncClient

**What:** The `/transcribe` FastAPI route calls Deepgram's REST API directly with `httpx.AsyncClient`. The frontend sends `{params, url}` or `{params, filename}`.

**When to use:** When the frontend calls `POST /transcribe`.

**Example:**
```python
# Source: httpx docs + Deepgram REST API (https://developers.deepgram.com/reference/listen-remote)
@fastapi_app.post("/transcribe")
async def transcribe(request: Request):
    body = await request.json()
    params = body.get("params", {})
    url = body.get("url")
    filename = body.get("filename")

    api_key = os.getenv("DEEPGRAM_API_KEY", "")
    clean = clean_params(params, Mode.BATCH)

    # Build Deepgram boolean query params as lowercase strings
    query_params = {}
    for k, v in clean.items():
        if isinstance(v, bool):
            query_params[k] = "true" if v else "false"
        else:
            query_params[k] = v
    query_params.setdefault("model", "nova-2")

    headers = {
        "Authorization": f"Token {api_key}",
    }

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            if url:
                # Remote URL: POST JSON body {"url": "..."}
                resp = await client.post(
                    "https://api.deepgram.com/v1/listen",
                    headers={**headers, "Content-Type": "application/json"},
                    params=query_params,
                    json={"url": url},
                )
            else:
                # Uploaded file: POST binary body
                file_path = TEMP_DIR / filename
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
```

### Pattern 3: Session State for File Streaming

**What:** File streaming uses the same `_sessions[sid]` dict shape as mic streaming. `on_start_file_streaming` creates the session entry and spawns `file_streaming_task()` as an `asyncio.Task`. `on_stop_file_streaming` sets `stop_event` to cancel early.

**Example:**
```python
@sio.on("start_file_streaming")
async def on_start_file_streaming(sid, data):
    if sid in _sessions:
        logger.warning("[%s] start_file_streaming while already streaming — ignoring", sid)
        return
    filename = data.get("filename")
    params = data.get("params", {})
    if not filename:
        await sio.emit("stream_error", {"message": "No filename provided"}, to=sid)
        return
    stop_event = asyncio.Event()
    _sessions[sid] = {"stop_event": stop_event, "ws": None, "request_id": None}
    task = asyncio.create_task(file_streaming_task(sid, filename, params, stop_event))
    _sessions[sid]["task"] = task


@sio.on("stop_file_streaming")
async def on_stop_file_streaming(sid, data=None):
    if sid not in _sessions:
        await sio.emit("stream_finished", {"request_id": None}, to=sid)
        return
    _sessions[sid]["stop_event"].set()
    # stream_finished emitted by file_streaming_task finally block
```

### Anti-Patterns to Avoid

- **Separate WebSocket management for files:** Do not build a new WS connection loop. Reuse `dg.listen.v1.connect()` and the same `start_listening()` + `send_close_stream()` pattern from Phase 3.
- **Blocking file I/O in async context:** Use synchronous `open()` inside the task but call it within the asyncio loop — for files pre-loaded to disk (small audio), this is acceptable. For very large files, consider `asyncio.to_thread()`. Given files are already fully uploaded to `TEMP_DIR`, synchronous `open` in an async task is acceptable.
- **requests import:** No `import requests` may appear in `app.py` or anywhere in the codebase. Use `httpx.AsyncClient` exclusively.
- **Not awaiting listen_task before stream_finished:** The critical fix from STR-04 applies here too. Always `await listen_task` after `send_close_stream()` to flush final words before emitting `stream_finished`.
- **Missing `stop_event` on EOF:** The file streaming task must set its own stop condition (EOF loop break), not just rely on `stop_event`. However, `stop_event` must also be honored to allow early cancellation.
- **Sending Deepgram a 0-byte chunk:** Check `if not chunk: break` before calling `ws.send_media(chunk)`. Sending an empty bytes object at EOF before `CloseStream` will not break anything but is semantically wrong.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Deepgram WS protocol framing | Custom WebSocket client | `dg.listen.v1.connect()` (existing) | SDK handles auth, encoding, CloseStream framing |
| Final-word flushing | Custom wait loop | `await listen_task` after `send_close_stream()` | This pattern is proven in Phase 3 (STR-04) |
| HTTP client for Deepgram REST | `requests.post()` | `httpx.AsyncClient.post()` | Already in deps; asyncio-native; `requests` is banned |
| Params serialization | Custom bool-to-string | `_params_to_sdk_kwargs()` (existing) + `clean_params(Mode.BATCH)` | These helpers already exist and are tested |

**Key insight:** Phase 3 solved the hardest problem (Deepgram WS lifecycle + final-word flushing). Phase 4 is largely plumbing to connect file bytes to the same pipe.

---

## Common Pitfalls

### Pitfall 1: Final Words Dropped at End-of-File

**What goes wrong:** `stream_finished` emitted before Deepgram flushes the last transcript result for the end of the file.

**Why it happens:** After the file loop ends, the code emits `stream_finished` immediately without waiting for Deepgram to process the last audio chunk. This is the same issue as STR-04.

**How to avoid:** Always call `await ws.send_close_stream()` after the file loop, then `await listen_task` before the `finally` block runs. The `finally` block emits `stream_finished`, so the sequence is: all bytes sent → `send_close_stream()` → `await listen_task` (flushes all Results) → `stream_finished` emitted.

**Warning signs:** Transcript cuts off 1-2 words before end of audio file.

### Pitfall 2: Missing `filename` in `start_file_streaming` Data

**What goes wrong:** `KeyError` or `NoneType` error when constructing the file path.

**Why it happens:** The frontend sends `{params, filename}` but the stub just logs and emits. The real handler must validate `filename` is present.

**How to avoid:** Early return with `stream_error` emit if `filename` is falsy.

**Warning signs:** `NoneType has no attribute ...` exceptions in the server log.

### Pitfall 3: httpx Timeout for Large Files

**What goes wrong:** Batch transcription times out for large audio files.

**Why it happens:** Default httpx timeout is 5 seconds; Deepgram can take 10-60+ seconds for long files.

**How to avoid:** Set `timeout=300.0` (5 minutes) on `httpx.AsyncClient`. This is the same pattern other Deepgram integrations use.

**Warning signs:** `httpx.ReadTimeout` error in batch transcription.

### Pitfall 4: Collision with Existing Mic Session

**What goes wrong:** File streaming starts when a mic streaming session is already active for the same `sid`.

**Why it happens:** Both use `_sessions[sid]`. If the user somehow triggers file streaming while a mic session is active, the existing session would be overwritten.

**How to avoid:** Guard with `if sid in _sessions: return` (already shown in the pattern). The frontend mode switching should prevent this, but the server must be defensive.

### Pitfall 5: Boolean Params in Batch Query String

**What goes wrong:** Deepgram ignores `punctuate=True` (Python bool) in query params.

**Why it happens:** Same as Phase 3 — Deepgram expects lowercase `"true"/"false"` strings, not Python booleans.

**How to avoid:** Apply the same bool-to-string conversion in the batch route as in `_params_to_sdk_kwargs()`. Use `"true" if v else "false"` for all boolean values in `query_params`.

**Warning signs:** Batch transcription ignores punctuation/smart_format settings that work in streaming mode.

### Pitfall 6: Content-Type for File vs. URL Batch

**What goes wrong:** Deepgram returns 400 or transcribes nothing.

**Why it happens:** URL batch requires `Content-Type: application/json` and a JSON body `{"url": "..."}`. File batch requires a binary body with the correct audio content type.

**How to avoid:** Branch on whether `url` or `filename` is provided. For files, read bytes and send as binary. `Content-Type: audio/*` is accepted by Deepgram as a catch-all.

---

## Code Examples

Verified patterns from source code inspection:

### Confirmed SDK Methods Available

```python
# Source: /coding/deepgram-python-stt/.venv2/lib/python3.12/site-packages/deepgram/listen/v1/socket_client.py
# AsyncV1SocketClient methods (all confirmed async):
await ws.send_media(chunk: bytes)        # send audio chunk
await ws.send_close_stream()             # signal EOF to Deepgram
await ws.send_keep_alive()               # prevent idle timeout
await ws.start_listening()              # blocking message loop (run as asyncio.Task)

# Source: /coding/deepgram-python-stt/.venv2/lib/python3.12/site-packages/deepgram/listen/v1/media/client.py
# AsyncMediaClient methods (alternative to direct httpx — SDK wraps httpx internally):
result = await dg.listen.v1.media.transcribe_url(url="https://...", model="nova-2", ...)
result = await dg.listen.v1.media.transcribe_file(request=file_bytes, model="nova-2", ...)
# Returns MediaTranscribeResponse (Pydantic model with .dict() method)
```

### Chunked File Reading Pattern

```python
# Source: Python stdlib + Phase 3 app.py patterns
CHUNK_SIZE = 4096  # 4KB chunks — balance between throughput and latency
with open(file_path, "rb") as f:
    while not stop_event.is_set():
        chunk = f.read(CHUNK_SIZE)
        if not chunk:
            break  # EOF
        await ws.send_media(chunk)
```

### httpx Batch Call Pattern

```python
# Source: httpx documentation + Deepgram REST API spec
import httpx

async with httpx.AsyncClient(timeout=300.0) as client:
    # For URL source:
    resp = await client.post(
        "https://api.deepgram.com/v1/listen",
        headers={"Authorization": f"Token {api_key}", "Content-Type": "application/json"},
        params={"model": "nova-2", "punctuate": "true"},
        json={"url": "https://example.com/audio.wav"},
    )
    resp.raise_for_status()
    data = resp.json()
```

### Existing `_params_to_sdk_kwargs` Reference (reuse for batch)

```python
# Source: app.py — already implemented in Phase 3
def _params_to_sdk_kwargs(raw_params: dict) -> dict:
    """Convert frontend params dict to deepgram-sdk 6.x keyword args."""
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

# For batch, call clean_params(params, Mode.BATCH) to get batch-compatible params
# Then apply the same bool-to-string conversion
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `requests.post()` for batch | `httpx.AsyncClient.post()` | Phase 1 (stub), Phase 4 (real impl) | asyncio-native, no thread blocking |
| Separate WebSocket code for file streaming | Reuse `streaming_task()` infrastructure | Phase 4 (new) | Less code, consistent behavior |

**Deprecated/outdated:**
- `requests` library: banned from pyproject.toml (STACK-01). Must not appear in any import.

---

## Open Questions

1. **Should file streaming use `asyncio.to_thread()` for file reads?**
   - What we know: Files in `TEMP_DIR` are pre-uploaded, typically < 50MB audio files. Reading 4KB chunks synchronously in an async task is a momentary block per chunk.
   - What's unclear: Whether this is a practical problem at typical audio file sizes (< 10MB).
   - Recommendation: Use synchronous `open()` inside the async task for simplicity. The blocks are microseconds per 4KB chunk. Use `asyncio.to_thread()` only if profiling reveals actual latency issues.

2. **Should the batch route use `dg.listen.v1.media.transcribe_url()` or raw `httpx`?**
   - What we know: Both work. The SDK's `AsyncMediaClient` methods are `async def` and use httpx internally. The requirement says "httpx.AsyncClient" explicitly (FILE-02).
   - What's unclear: Whether using the SDK's wrapper would satisfy the spirit of FILE-02.
   - Recommendation: Use `httpx.AsyncClient` directly per the requirement wording. This also avoids SDK type coercion issues and gives transparent control over headers, params, and error handling.

3. **What happens if the uploaded file no longer exists when streaming starts?**
   - What we know: Files are written to `TEMP_DIR` on upload. The `/upload` route saves them synchronously.
   - What's unclear: Whether files are cleaned up between upload and streaming (e.g., OS tmpdir cleanup).
   - Recommendation: Wrap `open(file_path, "rb")` in a try/except and emit `stream_error` if the file is missing.

---

## Sources

### Primary (HIGH confidence)
- Deepgram SDK source code at `/coding/deepgram-python-stt/.venv2/lib/python3.12/site-packages/deepgram/listen/v1/` — confirmed `AsyncV1SocketClient` methods, `AsyncMediaClient` methods, `connect()` signature
- `app.py` at `/coding/deepgram-python-stt/app.py` — confirmed current stub handlers, `streaming_task()` pattern, `_params_to_sdk_kwargs()`, `_sessions` dict shape
- `pyproject.toml` at `/coding/deepgram-python-stt/pyproject.toml` — confirmed `httpx>=0.27.0` is already a dependency
- `stt/options.py` — confirmed `clean_params(params, Mode.BATCH)` exists and strips streaming-only params

### Secondary (MEDIUM confidence)
- Frontend `static/app.js` — confirmed `start_file_streaming` sends `{params, filename}`, `stop_file_streaming` sends `{}`, batch `/transcribe` sends `{params, url}` or `{params, filename}` via fetch
- `tests/test_app.py` — confirmed `/transcribe` test expects 501 (stub), will need update to expect 200 in Phase 4
- Deepgram REST API pattern for URL source: `POST /v1/listen` with `Content-Type: application/json` and `{"url": "..."}` body

### Tertiary (LOW confidence - not verified against official docs)
- httpx timeout of 300s for large file batch: reasonable estimate based on Deepgram typical processing times

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries confirmed present in pyproject.toml and .venv2
- Architecture: HIGH — confirmed by reading actual SDK source, existing app.py patterns
- Pitfalls: HIGH — final-word pitfall confirmed from Phase 3 history; bool params pitfall confirmed from STATE.md
- Batch httpx pattern: MEDIUM — Deepgram REST API contract verified from SDK source and frontend JS, but not from official Deepgram docs directly

**Research date:** 2026-03-05
**Valid until:** 2026-04-05 (deepgram-sdk 6.0.1 is pinned; httpx API is stable)
