# Roadmap: deepgram-python-stt

## Overview

This roadmap covers milestone v2.0 — the migration from Flask/gevent/websocket-client to FastAPI/asyncio/deepgram-sdk. The five phases are ordered by a hard dependency chain: gevent must be eliminated before asyncio can function, the test harness must exist before SDK integration can be validated, and the core streaming infrastructure must exist before file streaming can reuse it. The result is a fully asyncio-native reference implementation of the Deepgram Python SDK deployed on Fly.io with zero frontend changes.

## Milestones

- 🚧 **v2.0 SDK Migration** — Phases 1-5 (in progress)

## Phases

### 🚧 v2.0 SDK Migration (In Progress)

**Milestone Goal:** Replace Flask/gevent/websocket-client with FastAPI/asyncio/deepgram-sdk 6.x, making the app a correct reference implementation of the SDK's async streaming API.

- [x] **Phase 1: Eliminate gevent + ASGI Skeleton** - Remove the gevent import-time bomb and stand up FastAPI + python-socketio + uvicorn with stubbed handlers (completed 2026-03-05)
- [x] **Phase 2: Async Test Infrastructure** - Establish UvicornTestServer + AsyncClient fixtures so every subsequent phase ships with tests (completed 2026-03-05)
- [ ] **Phase 3: Deepgram SDK Streaming** - Replace raw websocket-client with AsyncDeepgramClient.listen.v1.connect() for live mic transcription
- [ ] **Phase 4: File Streaming + Batch** - Extend async streaming infrastructure to file uploads; replace requests with httpx for batch
- [ ] **Phase 5: Test Coverage + Deployment** - Full test coverage on all async event handlers; Dockerfile and Fly.io deployment updated

## Phase Details

### Phase 1: Eliminate gevent + ASGI Skeleton
**Goal**: The app runs on uvicorn with FastAPI + python-socketio, gevent is gone, and all SocketIO event names are preserved with stubbed handlers
**Depends on**: Nothing (first phase)
**Requirements**: STACK-01, STACK-02, TRANS-01, TRANS-02
**Success Criteria** (what must be TRUE):
  1. `python -c "import app"` under uvicorn raises no gevent or monkey-patch import errors
  2. Browser can load the frontend at localhost and connect via SocketIO without 404
  3. All SocketIO event names (`toggle_transcription`, `audio_stream`, `transcription_update`, etc.) are registered and receive messages without error
  4. Flask, Flask-SocketIO, gevent, gevent-websocket, gunicorn, websocket-client, and requests are absent from pyproject.toml
  5. FastAPI, python-socketio[asyncio], uvicorn, and httpx are present in pyproject.toml
**Plans**: 2 plans

### Phase 2: Async Test Infrastructure
**Goal**: The test suite runs against the new ASGI stack using a real UvicornTestServer and socketio.AsyncClient, with at least one passing SocketIO connect test
**Depends on**: Phase 1
**Requirements**: TEST-01
**Success Criteria** (what must be TRUE):
  1. `pytest` runs without import errors against the new stack (no Flask-SocketIO test_client calls remain)
  2. A `UvicornTestServer` fixture starts and stops the real ASGI app within the test session
  3. A `socketio.AsyncClient` fixture connects to the test server and receives the `connect` event
  4. At least one full round-trip test passes: client connects, emits an event, receives a response
**Plans**: 1 plan

Plans:
- [ ] 02-01-PLAN.md — Install pytest-asyncio, fix stt imports, delete test_client.py, write conftest.py fixtures, rewrite test_app.py for ASGI stack

### Phase 3: Deepgram SDK Streaming
**Goal**: Live mic transcription works end-to-end via AsyncDeepgramClient.listen.v1.connect(), with per-session asyncio tasks, keep-alive, and clean stop
**Depends on**: Phase 2
**Requirements**: STR-01, STR-02, STR-03, STR-04
**Success Criteria** (what must be TRUE):
  1. Speaking into the browser mic produces `transcription_update` SocketIO events with correct `transcript` and `is_final` fields
  2. Clicking Stop waits for final results before closing — no words are dropped at the end of speech
  3. A 15-second speech pause does not silently disconnect the Deepgram WebSocket (keep-alive working)
  4. Starting, stopping, and starting transcription again in the same browser session works without error
  5. No threading primitives (`threading.Thread`, `threading.Event`, `time.sleep`) remain in the streaming path
**Plans**: 2 plans

Plans:
- [ ] 03-01-PLAN.md — Create test_streaming.py mock scaffold + implement streaming_task() and updated handlers in app.py
- [ ] 03-02-PLAN.md — Start dev server + human verification of live transcription, keep-alive, graceful stop, and start/stop/start cycle

### Phase 4: File Streaming + Batch
**Goal**: File upload streaming reuses the async streaming_task() infrastructure; batch /transcribe uses httpx.AsyncClient; final words are not dropped on file completion
**Depends on**: Phase 3
**Requirements**: FILE-01, FILE-02
**Success Criteria** (what must be TRUE):
  1. Uploading an audio file produces `transcription_update` events for all content, including the final words at end-of-file
  2. The `/transcribe` batch route returns a transcription result using httpx (no `requests` import remains)
  3. File streaming reuses `streaming_task()` from Phase 3 — no separate WebSocket management code exists for file uploads
**Plans**: TBD

### Phase 5: Test Coverage + Deployment
**Goal**: All async event handlers have test coverage, the Dockerfile launches uvicorn, and the app is verified running on Fly.io
**Depends on**: Phase 4
**Requirements**: TEST-02, DEPL-01, DEPL-02
**Success Criteria** (what must be TRUE):
  1. `pytest` passes with coverage across all SocketIO event handlers (`toggle_transcription`, `audio_stream`, file upload, batch transcription)
  2. Dockerfile CMD runs `uvicorn` (not gunicorn); `docker build` succeeds without error
  3. The deployed app at deepgram-python-stt.fly.dev accepts SocketIO connections and returns live transcriptions
**Plans**: TBD

## Progress

**Execution Order:** 1 → 2 → 3 → 4 → 5

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Eliminate gevent + ASGI Skeleton | 2/2 | Complete   | 2026-03-05 | - |
| 2. Async Test Infrastructure | 1/1 | Complete   | 2026-03-05 | - |
| 3. Deepgram SDK Streaming | v2.0 | 0/2 | Not started | - |
| 4. File Streaming + Batch | v2.0 | 0/TBD | Not started | - |
| 5. Test Coverage + Deployment | v2.0 | 0/TBD | Not started | - |
