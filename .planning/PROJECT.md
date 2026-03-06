# deepgram-python-stt

## What This Is

A browser-based live speech-to-text demo app. Users speak into their microphone (or upload an audio file), and the app streams audio to Deepgram for real-time transcription, displaying results in the browser. Intended as a reference implementation for Deepgram's Python SDK.

## Core Value

Accurate, real-time transcription from browser mic to screen with minimal latency — demonstrating the official Deepgram Python SDK pattern end-to-end.

## Requirements

### Validated

<!-- Shipped and confirmed valuable. -->

- ✓ Browser mic capture with MediaRecorder (WebM/Opus) — v1.0
- ✓ Real-time streaming to Deepgram via WebSocket — v1.0
- ✓ Live transcript display in browser — v1.0
- ✓ File upload + batch transcription — v1.0
- ✓ Alpine.js settings panel (model, language, diarization, etc.) — v1.0
- ✓ Client-side audio buffering until stream ready (prevents dropped WebM header) — v1.0
- ✓ Debug log panel with interim/final transcript tagging — v1.0
- ✓ Deepgram Python SDK (AsyncDeepgramClient) for streaming — v2.0
- ✓ FastAPI/asyncio backend (eliminates gevent monkey-patching) — v2.0
- ✓ python-socketio + uvicorn (async-compatible SocketIO) — v2.0
- ✓ Async test suite with UvicornTestServer + pytest-asyncio — v2.0

### Active

<!-- Current scope. Building toward these. -->

*(none — v2.0 complete)*

### Out of Scope

- Native mobile app — web-first
- User accounts / auth — demo app, single user
- Persistent transcript storage — session only

## Context

**Current stack (v2.0):**
- FastAPI + python-socketio (ASGIApp) + uvicorn
- deepgram-sdk 6.x (AsyncDeepgramClient) for all streaming
- pytest-asyncio + UvicornTestServer for async test coverage
- Alpine.js frontend (unchanged from v1.0), deployed on Fly.io

**v1.0 stack (archived):**
- Flask + Flask-SocketIO + gevent (monkey-patching)
- websocket-client (synchronous, gevent-patched) for Deepgram streaming
- deepgram-sdk installed but unused for streaming

**Deployment:** Fly.io (deepgram-python-stt.fly.dev), Dockerfile present

## Constraints

- **Frontend**: Keep existing Alpine.js frontend working — no JS rewrite
- **API**: Keep same SocketIO event names (connect, toggle_transcription, audio_stream, stream_started, transcription_update, etc.) so frontend requires zero changes
- **Deployment**: Must still deploy on Fly.io via Dockerfile
- **Python**: >=3.12, <3.13

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| websocket-client over deepgram-sdk in v1 | gevent incompatibility with asyncio | ✓ Replaced in v2.0 |
| gevent monkey-patching | Required for Flask-SocketIO sync model | ✓ Eliminated in v2.0 |
| FastAPI + uvicorn for v2 | Deepgram SDK requires asyncio; FastAPI is idiomatic async Python | ✓ Shipped v2.0 |
| Keep SocketIO event protocol | Frontend compatibility, zero JS changes needed | ✓ Zero frontend changes in v2.0 |
| socketio.ASGIApp wraps FastAPI | Pointing uvicorn directly at FastAPI causes 404s for SocketIO | ✓ Required pattern |
| UvicornTestServer for tests | AsyncServer has no test_client(); needs real server for SocketIO tests | ✓ Working test suite |
| SDK callbacks must be async def with **kwargs | sync or missing-kwargs callbacks silently never fire | ✓ Critical SDK contract |
| stream_started on WS connect, not Metadata | Metadata is non-deterministic (10s+ delay); connect is immediate | ✓ Correct UX behavior |
| Deepgram boolean params as lowercase strings | SDK/API rejects Python bools; "true"/"false" required | ✓ Required pattern |
| Audio timeslice 250ms | 1000ms chunks drop final words on Stop | ✓ Correct setting |

---
*Last updated: 2026-03-06 — Milestone v2.0 shipped*
