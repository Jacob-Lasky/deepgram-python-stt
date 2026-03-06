# Feature Landscape: Deepgram SDK v6.x Streaming Migration

**Domain:** SDK migration — raw websocket-client to deepgram-sdk 6.0.1 async streaming
**Researched:** 2026-03-05
**Confidence:** HIGH — all findings sourced from the installed SDK source at
`.venv/lib/python3.12/site-packages/deepgram/` (version 6.0.1, the exact version
pinned in pyproject.toml).

---

## Context: What the Current Implementation Does

The v1 `STTClient` in `stt/client.py` bypasses the SDK entirely for streaming:

- Opens a raw WebSocket with `websocket.create_connection()` (websocket-client library)
- Builds the Deepgram URL manually (`build_url()`)
- Runs a blocking `recv()` loop in a `threading.Thread`
- Parses raw JSON dicts manually (`data["channel"]["alternatives"][0]`)
- Fires plain Python callbacks: `on_transcript(data: dict, is_final: bool)`, `on_error(str)`, `on_close()`
- Sends audio with `ws.send_binary(data)`, close with `ws.send(json.dumps({"type": "CloseStream"}))`

The batch transcription path (`transcribe_batch`) uses `requests` directly — also bypassing the SDK.

---

## SDK Architecture Overview

The SDK exposes streaming via a two-level namespace:

```
AsyncDeepgramClient(api_key=...)
  .listen             # AsyncListenClient
    .v1               # AsyncV1Client  — classic streaming (this project uses this)
      .connect(...)   # async context manager → yields AsyncV1SocketClient
    .v2               # AsyncV2Client  — new "turn-based" API (agent use case)
      .connect(...)   # async context manager → yields AsyncV2SocketClient
```

**Use v1 for this migration.** V2 is a different protocol (`TurnInfo` events, `EndOfTurn`
semantics) intended for conversational agents, not live transcription demos.

---

## Table Stakes

Features the migration must preserve. Missing any = the app breaks or regresses.

| Feature | Current Implementation | SDK Equivalent | Complexity |
|---------|----------------------|----------------|------------|
| Open streaming WebSocket | `websocket.create_connection()` + manual URL | `async with client.listen.v1.connect(model=..., ...)` | Low |
| Send audio chunks | `ws.send_binary(bytes)` | `await socket.send_media(bytes)` | Low |
| Send CloseStream | `ws.send(json.dumps({"type":"CloseStream"}))` | `await socket.send_close_stream()` | Low |
| Receive Results (interim + final) | Manual JSON parse of `data["type"] == "Results"` | `EventType.MESSAGE` with typed `ListenV1Results` | Low |
| Distinguish interim vs final | `data.get("is_final", False)` on raw dict | `result.is_final` attribute on `ListenV1Results` | Low |
| Connection error handling | `try/except` in recv thread, calls `on_error(str)` | `EventType.ERROR` callback receives `Exception` | Low |
| Connection close detection | `finally` block in recv thread, calls `on_close()` | `EventType.CLOSE` callback | Low |
| Keep connection open | (not implemented, raw socket stays open) | `await socket.send_keep_alive()` — now explicit | Low |
| Finalize (flush) | Not implemented | `await socket.send_finalize()` | Low |
| Batch transcription | `requests.post()` directly | `client.listen.v1.media.transcribe_file()` — or keep existing | Low |
| Query param pass-through | `build_url()` + manual URL encoding | Named kwargs on `connect()` | Medium |

---

## SDK API: Complete Method and Event Reference

### Entry Point

```python
from deepgram import AsyncDeepgramClient

client = AsyncDeepgramClient(api_key="...")
# client.listen.v1 → AsyncV1Client
# client.listen.v2 → AsyncV2Client (not needed here)
```

`AsyncDeepgramClient` also accepts:
- `access_token` — uses Bearer token auth instead of `Token` auth
- `session_id` — sent as `x-deepgram-session-id` header on every request (auto-generated UUID if omitted)
- `transport_factory` — replaces the `websockets` transport entirely (useful for testing)

### Opening a Stream

```python
async with client.listen.v1.connect(
    model="nova-3",
    language="en",
    interim_results="true",
    smart_format="true",
    diarize="true",
    endpointing="300",
    utterance_end_ms="1000",
    vad_events="true",
    # ... all other Deepgram query params as str kwargs
) as socket:
    # socket is AsyncV1SocketClient
    ...
```

**Important:** All query parameters are typed `Optional[str]` in the SDK signature, not
booleans or ints. Pass `"true"` not `True`, `"300"` not `300`. The existing
`clean_params()` logic in `stt/options.py` handles bool-to-string conversion but will
need adaptation since the SDK takes named kwargs, not a dict.

The `connect()` method is an `@asynccontextmanager`. It:
1. Builds the WSS URL from the Deepgram environment base URL
2. Calls `websockets_client_connect(ws_url, extra_headers=headers)` (the `websockets` library, not `websocket-client`)
3. Yields `AsyncV1SocketClient(websocket=protocol)` on success
4. Raises `ApiError` with `status_code=401` on auth failure, or generic `ApiError` on other WebSocket errors

### Sending Data

```python
await socket.send_media(audio_bytes: bytes)         # send audio chunk
await socket.send_close_stream()                    # signal end of stream
await socket.send_keep_alive()                      # prevent idle timeout
await socket.send_finalize()                        # flush buffered audio immediately
```

All four methods exist. The current STTClient only implements `send_media` and `send_close_stream`. `send_keep_alive` and `send_finalize` are new capabilities the SDK makes accessible.

### Receiving Transcripts: Two Patterns

**Pattern A — Event Emitter (callback-based, mirrors current approach):**

```python
from deepgram.core.events import EventType

def on_open(data):      # data is None
    ...

def on_message(result): # result is Union[ListenV1Results, ListenV1Metadata,
    ...                 #   ListenV1UtteranceEnd, ListenV1SpeechStarted]

def on_error(exc):      # exc is Exception
    ...

def on_close(data):     # data is None
    ...

socket.on(EventType.OPEN,    on_open)
socket.on(EventType.MESSAGE, on_message)
socket.on(EventType.ERROR,   on_error)
socket.on(EventType.CLOSE,   on_close)

await socket.start_listening()   # blocks until connection closes
```

Callbacks can be sync or async — `_emit_async` awaits them if they return a coroutine.

**Pattern B — Async Iterator (Pythonic, easier for asyncio):**

```python
async for message in socket:
    # message is Union[ListenV1Results, ListenV1Metadata,
    #   ListenV1UtteranceEnd, ListenV1SpeechStarted, bytes]
    if isinstance(message, ListenV1Results):
        ...
```

**Pattern C — Manual recv (pull-based):**

```python
result = await socket.recv()   # returns one typed message
```

**Recommendation for this project:** Pattern A (event emitter) maps most cleanly onto
the existing callback architecture in `app.py` and keeps the new `STTClient` interface
similar to the current one. Pattern B is cleaner for standalone scripts but harder to
integrate with python-socketio's concurrent model.

### Typed Response Objects

All messages received via the SDK are Pydantic models (frozen, extra fields allowed):

**`ListenV1Results`** — the main transcript event:
```
.type              → Literal["Results"]
.is_final          → Optional[bool]   — True when transcript is finalized
.speech_final      → Optional[bool]   — True when Deepgram endpoint detected
.from_finalize     → Optional[bool]   — True when triggered by send_finalize()
.start             → float            — audio segment start time (seconds)
.duration          → float            — audio segment duration (seconds)
.channel_index     → List[float]
.channel           → ListenV1ResultsChannel
  .alternatives    → List[ListenV1ResultsChannelAlternativesItem]
    [0].transcript → str
    [0].confidence → float
    [0].words      → List[ListenV1ResultsChannelAlternativesItemWordsItem]
       .word           → str
       .start          → float
       .end            → float
       .confidence     → float
       .punctuated_word → Optional[str]
       .speaker        → Optional[float]   — speaker ID when diarize=true
       .language       → Optional[str]
.entities          → Optional[List[...]]  — only in is_final, when detect_entities=true
.metadata          → ListenV1ResultsMetadata
```

**`ListenV1Metadata`** — sent once at connection open:
```
.type            → Literal["Metadata"]
.transaction_key → str
.request_id      → str
.sha256          → str
.created         → str
.duration        → float
.channels        → float
```

**`ListenV1UtteranceEnd`** — fired when `utterance_end_ms` is set and silence detected:
```
.type          → Literal["UtteranceEnd"]
.channel       → List[float]
.last_word_end → float
```

**`ListenV1SpeechStarted`** — fired when `vad_events=true` and speech detected:
```
.type      → Literal["SpeechStarted"]
.channel   → List[float]
.timestamp → float
```

### Dispatch Pattern in on_message

```python
from deepgram.listen.v1.types import (
    ListenV1Results,
    ListenV1Metadata,
    ListenV1UtteranceEnd,
    ListenV1SpeechStarted,
)

def on_message(msg):
    if isinstance(msg, ListenV1Results):
        alt = msg.channel.alternatives[0]
        transcript = alt.transcript
        is_final = msg.is_final
        speaker = alt.words[0].speaker if alt.words else None
    elif isinstance(msg, ListenV1SpeechStarted):
        ...   # VAD event — speech detected
    elif isinstance(msg, ListenV1UtteranceEnd):
        ...   # utterance boundary signal
    elif isinstance(msg, ListenV1Metadata):
        ...   # request_id available here
```

**vs current approach** — current code checks `data.get("type") == "Results"` on a
raw dict. SDK replaces that with `isinstance()` on typed models. Attribute access
(`msg.is_final`) replaces dict access (`data.get("is_final")`).

---

## Differentiators

New capabilities the SDK exposes that the current raw implementation does not support:

| Feature | SDK API | Value | Complexity |
|---------|---------|-------|------------|
| `send_keep_alive()` | `await socket.send_keep_alive()` | Prevents idle timeout on long pauses; current impl may drop connection silently | Low |
| `send_finalize()` | `await socket.send_finalize()` | Force-flushes Deepgram's audio buffer immediately; useful for file streaming end | Low |
| `SpeechStarted` events | `EventType.MESSAGE` with `ListenV1SpeechStarted` | VAD-based speech detection events; currently silently ignored | Low |
| `UtteranceEnd` events | `EventType.MESSAGE` with `ListenV1UtteranceEnd` | Explicit utterance boundary signal; better than inferring from `speech_final` | Low |
| `from_finalize` flag | `msg.from_finalize` on `ListenV1Results` | Know when a result was triggered by finalize vs natural speech end | Low |
| `speech_final` flag | `msg.speech_final` on `ListenV1Results` | Endpoint detection — distinct from `is_final`; useful for agent turn-taking | Low |
| Entity detection | `msg.entities` on `ListenV1Results` | Named entities when `detect_entities=true`; currently parsed raw from dict but not surfaced | Low |
| `request_id` from Metadata | `msg.request_id` on `ListenV1Metadata` | Can surface real request_id in `stream_started` event instead of `None` | Low |
| Transport factory override | `AsyncDeepgramClient(transport_factory=...)` | Swap WebSocket transport for testing without mocking the entire SDK | Medium |
| `session_id` header | Auto-generated UUID per client instance | Correlate all requests from one session in Deepgram logs | Low |

---

## Anti-Features

Do not build these during the SDK migration:

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| v2 Listen API (`listen.v2`) | Different protocol (TurnInfo/EndOfTurn), designed for conversational agents, not live transcription | Use `listen.v1` only |
| Custom URL construction | SDK builds the URL from named params; manual URL building defeats the purpose of the migration | Pass params as `connect()` kwargs |
| Rewriting frontend SocketIO events | PROJECT.md constraint: keep same event names so frontend requires zero changes | Preserve all existing event names verbatim |
| Replacing the `stt/options.py` module entirely | `clean_params()` logic is still needed to translate the frontend's param dict into SDK kwargs | Adapt it to return SDK kwargs instead of a URL-encoded dict |
| Synchronous SDK path (`V1Client`, `V1SocketClient`) | The sync path uses `websockets.sync.client` which still blocks — defeating the point of moving to asyncio | Use `AsyncV1Client` / `AsyncV1SocketClient` throughout |
| Thread-per-stream model | Current threading model is a gevent workaround; SDK + asyncio replaces it with async tasks | Use `asyncio.create_task()` for the listen loop |
| Keeping `websocket-client` dependency | No longer needed once migration is complete | Remove from pyproject.toml after migration |

---

## What Changes vs What Stays

### Changes (in `stt/client.py` and `app.py`)

| Current | SDK Replacement |
|---------|----------------|
| `import websocket` (websocket-client) | `from deepgram import AsyncDeepgramClient` |
| `websocket.create_connection(url, header=...)` | `async with client.listen.v1.connect(model=..., ...)` |
| Manual URL build in `build_url()` | SDK builds URL from named kwargs |
| `threading.Thread(target=recv_loop)` | `asyncio.create_task(socket.start_listening())` |
| `ws.send_binary(data)` | `await socket.send_media(data)` |
| `json.dumps({"type": "CloseStream"})` via `ws.send()` | `await socket.send_close_stream()` |
| `json.loads(msg)` + `data.get("type")` | `isinstance(msg, ListenV1Results)` |
| `data["channel"]["alternatives"][0]["transcript"]` | `msg.channel.alternatives[0].transcript` |
| `data.get("is_final", False)` | `msg.is_final` |
| `alt.get("words", [])[0].get("speaker")` | `msg.channel.alternatives[0].words[0].speaker` |
| `on_transcript(data: dict, is_final: bool)` signature | `on_message(msg: ListenV1Results \| ...)` |
| Flask + gevent | FastAPI + uvicorn (separate milestone concern but required by SDK) |
| Flask-SocketIO + gevent | python-socketio + uvicorn (required by SDK) |

### Stays the Same

| Item | Reason |
|------|--------|
| SocketIO event names: `connect`, `toggle_transcription`, `audio_stream`, `stream_started`, `transcription_update`, `stream_error`, `stream_finished`, `detect_audio_settings`, `audio_settings` | Frontend compatibility constraint |
| `transcription_update` payload shape: `{transcript, is_final, speaker, response}` | Frontend reads these fields |
| `stream_started` payload shape: `{request_id, url}` | `request_id` can be real now (from Metadata) |
| `stt/options.py` `Mode` enum and `STREAMING_ONLY` / `BATCH_ONLY` sets | Logic is still valid for param filtering |
| Per-session state dict keyed by SocketIO `sid` | Necessary for multi-client isolation |
| File upload + batch transcription flow | Out of scope for this migration (or trivial to keep using `requests`) |
| Alpine.js frontend | Zero JS changes required |
| Fly.io deployment via Dockerfile | Just update app server from gunicorn/gevent to uvicorn |

---

## Connection Lifecycle Comparison

### Current (raw websocket-client)

```
1. build_url(params)  →  construct wss://... URL manually
2. websocket.create_connection(url, header={...})  →  synchronous, blocks until connected
3. Thread(recv_loop).start()  →  background thread reads messages forever
4. send_binary(chunk)  →  send audio (from any thread)
5. send(CloseStream JSON)  →  signal end
6. recv_loop exits  →  calls on_close()  →  thread dies
```

Failure modes not handled: idle timeout (no KeepAlive), no way to flush buffered audio.

### SDK (deepgram-sdk 6.0.1 async)

```
1. AsyncDeepgramClient(api_key=...)  →  create client (cheap, no connection yet)
2. async with client.listen.v1.connect(model=..., ...)  →  opens WSS, yields socket
   - Raises ApiError(401) on auth failure
   - Raises ApiError on other WebSocket failures
3. socket.on(EventType.MESSAGE, handler)  →  register callbacks
4. asyncio.create_task(socket.start_listening())  →  async task reads messages
   - Emits OPEN immediately
   - Emits MESSAGE for each Results/Metadata/UtteranceEnd/SpeechStarted
   - Emits ERROR on exception
   - Emits CLOSE when loop exits
5. await socket.send_media(chunk)  →  send audio (from async context)
6. await socket.send_keep_alive()  →  optional, prevent idle timeout
7. await socket.send_finalize()  →  optional, flush buffer
8. await socket.send_close_stream()  →  signal end
9. Context manager exits  →  closes underlying websocket
```

The context manager exit (`async with` block end) handles the final WebSocket `close()`.
The CLOSE event fires from within `start_listening()` when the recv loop ends naturally.

---

## `clean_params()` Adaptation

The existing `clean_params()` returns a `dict` for URL encoding. The SDK's `connect()`
takes named keyword arguments instead. The adaptation needed:

```python
# Current usage (manual URL):
clean = clean_params(params, Mode.STREAMING)
url = build_url(clean)

# SDK usage:
clean = clean_params(params, Mode.STREAMING)
# Pass **clean to connect(), but SDK takes str not bool/int:
sdk_params = {k: str(v).lower() if isinstance(v, bool) else str(v)
              for k, v in clean.items()}
async with client.listen.v1.connect(model=sdk_params.pop("model"), **sdk_params):
    ...
```

The `model` param is the only required kwarg on `connect()`. All others are optional.

---

## Sources

All findings are sourced from the installed package source, confidence HIGH:

- `/coding/deepgram-python-stt/.venv/lib/python3.12/site-packages/deepgram/listen/v1/socket_client.py` — `AsyncV1SocketClient` class, all send/recv methods
- `/coding/deepgram-python-stt/.venv/lib/python3.12/site-packages/deepgram/listen/v1/client.py` — `AsyncV1Client.connect()` context manager, full param list
- `/coding/deepgram-python-stt/.venv/lib/python3.12/site-packages/deepgram/core/events.py` — `EventEmitterMixin`, `EventType` enum
- `/coding/deepgram-python-stt/.venv/lib/python3.12/site-packages/deepgram/listen/v1/types/` — all Pydantic response models
- `/coding/deepgram-python-stt/.venv/lib/python3.12/site-packages/deepgram/client.py` — `AsyncDeepgramClient`, `session_id`, `access_token`, `transport_factory`
- `/coding/deepgram-python-stt/.venv/lib/python3.12/site-packages/deepgram/listen/client.py` — `AsyncListenClient`, `.v1` and `.v2` properties
- `/coding/deepgram-python-stt/.venv/lib/python3.12/site-packages/deepgram/listen/v2/` — v2 API (TurnInfo, not used)
- `/coding/deepgram-python-stt/stt/client.py` — current raw implementation (v1 baseline)
- `/coding/deepgram-python-stt/app.py` — current SocketIO event wiring (what must stay the same)
