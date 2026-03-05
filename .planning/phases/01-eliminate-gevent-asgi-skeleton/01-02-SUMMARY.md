---
phase: 01-eliminate-gevent-asgi-skeleton
plan: 02
subsystem: api
tags: [fastapi, python-socketio, uvicorn, asgi, gevent-removal]

# Dependency graph
requires:
  - phase: 01-eliminate-gevent-asgi-skeleton
    plan: 01
    provides: "fastapi, uvicorn, python-socketio[asyncio] installed in virtualenv"

provides:
  - "app.py rewritten as ASGI skeleton with FastAPI + python-socketio AsyncServer"
  - "All 7 SocketIO events registered as stubbed async coroutines"
  - "socketio.ASGIApp(sio, fastapi_app) as uvicorn entry point"
  - "HTTP routes: GET /, POST /upload, POST /transcribe (501 stub)"

affects: [02-tests, 03-deepgram-sdk, 04-transcribe-endpoint]

# Tech tracking
tech-stack:
  added: [python-multipart>=0.0.9 (required by FastAPI UploadFile)]
  patterns:
    - "sio = socketio.AsyncServer(async_mode='asgi') — always asgi mode"
    - "app = socketio.ASGIApp(sio, fastapi_app) — uvicorn target is the ASGI wrapper not fastapi_app"
    - "await sio.emit('event', data, to=sid) — to=sid not room=sid"
    - "All SocketIO handlers are async def (mandatory for AsyncServer)"

key-files:
  created: []
  modified:
    - app.py
    - pyproject.toml
    - uv.lock

key-decisions:
  - "python-multipart added as dependency — FastAPI UploadFile requires it at route registration time (not just request time)"
  - "app variable points at socketio.ASGIApp wrapper — if it pointed at fastapi_app, /socket.io/ endpoint would 404"

patterns-established:
  - "ASGI wiring: socketio.ASGIApp wraps fastapi_app so both HTTP (FastAPI) and SocketIO are served by one uvicorn process"
  - "Stub pattern: all event handlers are functional stubs that emit the expected lifecycle events (stream_started/stream_finished) so frontend does not hang"

requirements-completed: [TRANS-01, TRANS-02]

# Metrics
duration: 5min
completed: 2026-03-05
---

# Phase 01 Plan 02: ASGI Skeleton Summary

**app.py rewritten from Flask/gevent to FastAPI + python-socketio AsyncServer (async_mode=asgi) with all 7 SocketIO event stubs and correct socketio.ASGIApp wiring**

## Performance

- **Duration:** ~5 min
- **Started:** 2026-03-05T14:26:44Z
- **Completed:** 2026-03-05T14:31:00Z
- **Tasks:** 2
- **Files modified:** 3 (app.py, pyproject.toml, uv.lock)

## Accomplishments

- Deleted gevent monkey.patch_all() and all Flask/Flask-SocketIO/stt.client imports
- Registered all 7 SocketIO events as async coroutines: connect, disconnect, toggle_transcription, audio_stream, detect_audio_settings, start_file_streaming, stop_file_streaming
- Wired socketio.ASGIApp(sio, fastapi_app) as the uvicorn entry point
- HTTP smoke test confirmed: GET / -> 200, GET /socket.io/?EIO=4&transport=polling -> 200

## Task Commits

Each task was committed atomically:

1. **Task 1: Delete gevent lines and write new app.py skeleton** - `02a51cb` (feat)
2. **Task 2: Smoke test the running server** - no file changes (read-only verification)

**Plan metadata:** (docs commit follows)

## Import Test Result

```
uv run python -c "import app; print('import OK')"
import OK
```

No gevent errors, no Flask errors, no stt.client errors.

## HTTP Smoke Test Results

| Endpoint | Status Code | Expected |
|----------|-------------|----------|
| GET / | 200 | 200 |
| GET /socket.io/?EIO=4&transport=polling | 200 | 200 |

## Files Created/Modified

- `/coding/deepgram-python-stt/app.py` - Full rewrite: FastAPI + python-socketio AsyncServer, 7 SocketIO stubs, 109 lines
- `/coding/deepgram-python-stt/pyproject.toml` - Added python-multipart>=0.0.9 dependency
- `/coding/deepgram-python-stt/uv.lock` - Updated lockfile with python-multipart 0.0.22

## Decisions Made

- `app = socketio.ASGIApp(sio, fastapi_app)` is the uvicorn target, not fastapi_app directly. This is the critical wiring that makes SocketIO reachable.
- Added python-multipart as a dependency — FastAPI raises RuntimeError at route registration (not request time) if it's missing when UploadFile is used.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Added missing python-multipart dependency**
- **Found during:** Task 1 (app.py import test)
- **Issue:** FastAPI's UploadFile route decorator raises RuntimeError at import time if python-multipart is not installed. `uv run python -c "import app"` exited 1.
- **Fix:** Added `python-multipart>=0.0.9,<1` to pyproject.toml dependencies, ran `uv sync`
- **Files modified:** pyproject.toml, uv.lock
- **Verification:** `uv run python -c "import app; print('import OK')"` exits 0
- **Committed in:** 02a51cb (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking dependency)
**Impact on plan:** Required for FastAPI UploadFile to work. Not scope creep — python-multipart is an implicit FastAPI requirement.

## Issues Encountered

- Port 8001 was occupied during Task 2 smoke test (leftover from a prior run), but the existing server was from the new app.py so it correctly served the endpoints. Both HTTP status codes confirmed as 200.

## Next Phase Readiness

- app.py is the clean ASGI entry point ready for Phase 2 test scaffolding
- All 7 SocketIO events are registered and testable
- uvicorn can serve `app:app` targeting the correct socketio.ASGIApp wrapper
- No blockers for plan 03

---
*Phase: 01-eliminate-gevent-asgi-skeleton*
*Completed: 2026-03-05*
