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

### Active

<!-- Current scope. Building toward these. -->

- [ ] Use Deepgram Python SDK (deepgram-sdk) for streaming instead of raw websocket-client
- [ ] Replace Flask/gevent with FastAPI/asyncio (required for SDK async API)
- [ ] Replace Flask-SocketIO/gevent with async-compatible SocketIO (python-socketio + uvicorn)
- [ ] Tests updated to cover new async stack

### Out of Scope

- Native mobile app — web-first
- User accounts / auth — demo app, single user
- Persistent transcript storage — session only

## Context

**Current stack (v1.0):**
- Flask + Flask-SocketIO + gevent (async via monkey-patching)
- websocket-client (synchronous, gevent-patched) for Deepgram streaming
- deepgram-sdk 6.0.1 is installed but NOT used for streaming — only imported
- Manual URL construction + binary WebSocket protocol
- Alpine.js frontend, deployed on Fly.io

**Why migrate:**
- The repo's stated purpose is to be a reference implementation for the Deepgram Python SDK
- The current implementation bypasses the SDK entirely for the core streaming feature
- deepgram-sdk uses asyncio natively; Flask/gevent's monkey-patching conflicts with it
- FastAPI + uvicorn is the idiomatic async Python web stack

**Deployment:** Fly.io (deepgram-python-stt.fly.dev), Dockerfile present

## Constraints

- **Frontend**: Keep existing Alpine.js frontend working — no JS rewrite
- **API**: Keep same SocketIO event names (connect, toggle_transcription, audio_stream, stream_started, transcription_update, etc.) so frontend requires zero changes
- **Deployment**: Must still deploy on Fly.io via Dockerfile
- **Python**: >=3.12, <3.13

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| websocket-client over deepgram-sdk in v1 | gevent incompatibility with asyncio | ⚠️ Revisit — reason for v2 migration |
| gevent monkey-patching | Required for Flask-SocketIO sync model | ⚠️ Revisit — eliminated in v2 |
| FastAPI for v2 | Deepgram SDK requires asyncio; FastAPI is idiomatic async Python | — Pending |
| Keep SocketIO event protocol | Frontend compatibility, zero JS changes needed | — Pending |

---
*Last updated: 2026-03-05 — Milestone v2.0 started*
