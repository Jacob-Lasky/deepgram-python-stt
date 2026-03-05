# Requirements: deepgram-python-stt

**Defined:** 2026-03-05
**Core Value:** Real-time browser mic → Deepgram SDK → live transcript, as a reference implementation of deepgram-sdk 6.x

## v2 Requirements

### Stack

- [x] **STACK-01**: App removes Flask, Flask-SocketIO, gevent, gevent-websocket, gunicorn, websocket-client, and requests from dependencies
- [x] **STACK-02**: App adds FastAPI, python-socketio[asyncio], uvicorn, and httpx

### Transport

- [x] **TRANS-01**: App is served via `socketio.ASGIApp` wrapping FastAPI on uvicorn (single worker)
- [x] **TRANS-02**: All existing SocketIO event names and payload shapes are preserved verbatim (zero frontend changes required)

### Streaming

- [ ] **STR-01**: Live mic streaming uses `AsyncDeepgramClient.listen.v1.connect()` (deepgram-sdk 6.x async API)
- [ ] **STR-02**: Per-session Deepgram connection runs as an `asyncio.Task`, replacing threading.Thread
- [ ] **STR-03**: `send_keep_alive()` sent periodically to prevent Deepgram idle timeout during speech pauses
- [ ] **STR-04**: Stream stop waits for final results before closing (no dropped final words)

### File & Batch

- [ ] **FILE-01**: File upload streaming reuses the same async streaming infrastructure as mic streaming
- [ ] **FILE-02**: Batch `/transcribe` route uses `httpx.AsyncClient` instead of `requests`

### Testing

- [ ] **TEST-01**: Test suite uses `UvicornTestServer` + `socketio.AsyncClient` fixtures (replaces Flask-SocketIO test client)
- [ ] **TEST-02**: All existing test scenarios covered and passing on the new async stack

### Deployment

- [ ] **DEPL-01**: Dockerfile CMD updated from gunicorn to uvicorn
- [ ] **DEPL-02**: App deploys and runs successfully on Fly.io (deepgram-python-stt.fly.dev)

## Future Requirements

### QOL

- **QOL-01**: Scrollable settings panel (left panel params sections can't be scrolled)
- **QOL-02**: Expandable interim transcript debug sub-panel (time-stamped, collapsible, collapsed by default)

## Out of Scope

| Feature | Reason |
|---------|--------|
| Frontend/Alpine.js changes | Hard constraint — browser compatibility preserved verbatim |
| deepgram-sdk v2 Listen API | Different protocol for conversational agents, not live transcription |
| Multi-worker deployment | SocketIO in-memory state is not cross-process safe |
| User accounts / auth | Demo app, single user |
| Persistent transcript storage | Session only |
| Mobile app | Web-first |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| STACK-01 | Phase 1 | Complete |
| STACK-02 | Phase 1 | Complete |
| TRANS-01 | Phase 1 | Complete |
| TRANS-02 | Phase 1 | Complete |
| TEST-01 | Phase 2 | Pending |
| TEST-02 | Phase 5 | Pending |
| STR-01 | Phase 3 | Pending |
| STR-02 | Phase 3 | Pending |
| STR-03 | Phase 3 | Pending |
| STR-04 | Phase 3 | Pending |
| FILE-01 | Phase 4 | Pending |
| FILE-02 | Phase 4 | Pending |
| DEPL-01 | Phase 5 | Pending |
| DEPL-02 | Phase 5 | Pending |

**Coverage:**
- v2 requirements: 14 total
- Mapped to phases: 14
- Unmapped: 0 ✓

---
*Requirements defined: 2026-03-05*
*Last updated: 2026-03-05 after initial definition*
