---
phase: 02-async-test-infrastructure
plan: "01"
subsystem: testing
tags: [pytest-asyncio, socketio, uvicorn, aiohttp, httpx, asgi, websocket]

# Dependency graph
requires:
  - phase: 01-eliminate-gevent-asgi-skeleton
    provides: "socketio.ASGIApp (app) + FastAPI (fastapi_app) + 7 SocketIO event stubs in app.py"
provides:
  - "UvicornTestServer class: session-scoped uvicorn.Server subclass that starts the real ASGI app"
  - "conftest.py server fixture: session-scoped, starts/stops UvicornTestServer once per test session"
  - "conftest.py sio_client fixture: session-scoped socketio.AsyncClient connected to test server"
  - "test_app.py: 3 async HTTP tests via ASGITransport + 3 async SocketIO round-trip tests"
  - "Clean pytest collection: no import errors, 15 passing tests, 1 skipped"
affects: [03-deepgram-sdk-integration]

# Tech tracking
tech-stack:
  added:
    - "pytest-asyncio>=0.23,<1.0 (0.26.0 installed)"
    - "aiohttp>=3.9.0 (required by socketio.AsyncClient for WebSocket transport)"
    - "asyncio_mode = auto in pyproject.toml"
    - "asyncio_default_fixture_loop_scope = session in pyproject.toml"
    - "asyncio_default_test_loop_scope = session in pyproject.toml"
  patterns:
    - "UvicornTestServer subclasses uvicorn.Server, overrides startup(sockets=None) to set asyncio.Event on ready"
    - "Session-scoped async fixtures via pytest_asyncio.fixture(scope='session', loop_scope='session')"
    - "HTTP tests use ASGITransport(app=fastapi_app) — no port binding, no race conditions"
    - "SocketIO tests use real UvicornTestServer on port 9765 with socketio.AsyncClient"
    - "future + if not future.done() guard for session-scoped SocketIO event handlers"

key-files:
  created:
    - "tests/conftest.py"
  modified:
    - "tests/test_app.py"
    - "pyproject.toml"
    - "stt/__init__.py"
  deleted:
    - "tests/test_client.py"

key-decisions:
  - "Test server port changed from 8765 to 9765 — port 8765 is occupied by node /mobile/server.js (PID 1) in this container environment"
  - "pytest-asyncio pinned to <1.0 — version 1.3.0 changed session fixture scoping behavior, causing double-instantiation of UvicornTestServer and OSError: address in use"
  - "asyncio_default_test_loop_scope = session required alongside asyncio_default_fixture_loop_scope = session to prevent function-scoped loops from re-instantiating session fixtures"
  - "aiohttp installed as dev dependency — socketio.AsyncClient requires it for WebSocket transport (without it: 'aiohttp package not installed' and connection failure)"
  - "stt/__init__.py lazy import via TYPE_CHECKING — prevents requests ModuleNotFoundError at test collection time"
  - "tests/test_client.py deleted — tests STTClient class being replaced in Phase 3, deletion unblocks clean collection"

patterns-established:
  - "UvicornTestServer pattern: subclass uvicorn.Server, override startup(sockets=None) to signal ready via asyncio.Event, run via create_task(serve())"
  - "HTTP testing: use ASGITransport(app=fastapi_app) with httpx.AsyncClient — faster, no port binding"
  - "SocketIO testing: use session-scoped socketio.AsyncClient + real UvicornTestServer for integration fidelity"
  - "Event handler guard: if not future.done() on session-scoped handlers prevents InvalidStateError on re-fire"

requirements-completed:
  - TEST-01

# Metrics
duration: 15min
completed: "2026-03-05"
---

# Phase 2 Plan 01: Async Test Infrastructure Summary

**pytest-asyncio session fixtures with UvicornTestServer + socketio.AsyncClient enabling real ASGI integration tests for HTTP routes and SocketIO round-trips**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-03-05T16:10:07Z
- **Completed:** 2026-03-05T16:25:00Z
- **Tasks:** 2
- **Files modified:** 5 (conftest.py created, test_app.py rewritten, pyproject.toml updated, stt/__init__.py fixed, test_client.py deleted)

## Accomplishments
- UvicornTestServer fixture starts and stops the real ASGI app once per test session
- socketio.AsyncClient connects to the test server and receives connect event
- Full SocketIO round-trip tests pass: emit toggle_transcription start/stop, receive stream_started/stream_finished with request_id key
- HTTP tests via ASGITransport pass without requiring a live server port
- Test collection is clean: no import errors, 15 passing, 1 skipped

## Task Commits

Each task was committed atomically:

1. **Task 1: Fix test infrastructure prerequisites** - `2ef33a6` (chore)
2. **Task 2: Write conftest.py fixtures and rewrite test_app.py** - `45e99fd` (feat)

**Plan metadata:** (docs commit follows)

## Files Created/Modified
- `tests/conftest.py` - UvicornTestServer class + session-scoped server and sio_client fixtures
- `tests/test_app.py` - Rewritten: 3 async HTTP tests (ASGITransport) + 3 async SocketIO tests
- `pyproject.toml` - Added pytest-asyncio, aiohttp dev deps; asyncio_mode=auto, loop scope settings
- `stt/__init__.py` - Lazy TYPE_CHECKING import to prevent requests import at collection time
- `tests/test_client.py` - Deleted (obsolete STTClient tests, will be replaced in Phase 3)

## Decisions Made
- Test server port 9765 (not 8765 from plan) because port 8765 is occupied by `node /mobile/server.js` in this container environment
- pytest-asyncio pinned to <1.0 because 1.3.0 changed session fixture behavior, causing double-instantiation of UvicornTestServer
- aiohttp installed as dev dependency (not documented in plan) because socketio.AsyncClient requires it for WebSocket transport
- asyncio_default_test_loop_scope = session added alongside asyncio_default_fixture_loop_scope = session to prevent function-scoped loops re-instantiating session fixtures

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Changed test server port from 8765 to 9765**
- **Found during:** Task 2 (running tests)
- **Issue:** Port 8765 is permanently bound by `node /mobile/server.js` (PID 1) in this container. OSError: address already in use on first test run.
- **Fix:** Changed `PORT = 8765` to `PORT = 9765` in conftest.py
- **Files modified:** tests/conftest.py
- **Verification:** Tests run without port conflict
- **Committed in:** 45e99fd (Task 2 commit)

**2. [Rule 3 - Blocking] Downgraded pytest-asyncio from 1.3.0 to 0.26.0**
- **Found during:** Task 2 (running tests)
- **Issue:** pytest-asyncio 1.3.0 changed session fixture scoping — UvicornTestServer was being instantiated twice (different object addresses), second startup failed with "address already in use"
- **Fix:** Pinned `pytest-asyncio>=0.23,<1.0` in pyproject.toml; uv installed 0.26.0
- **Files modified:** pyproject.toml, uv.lock
- **Verification:** Session fixtures instantiated once, all tests pass
- **Committed in:** 45e99fd (Task 2 commit)

**3. [Rule 3 - Blocking] Added aiohttp as dev dependency**
- **Found during:** Task 2 (running socketio tests)
- **Issue:** socketio.AsyncClient requires aiohttp for WebSocket transport. Error: "aiohttp package not installed", connection failed.
- **Fix:** `uv add --dev "aiohttp>=3.9.0"` (3.13.3 installed)
- **Files modified:** pyproject.toml, uv.lock
- **Verification:** socketio.AsyncClient connects successfully, test_socketio_connects passes
- **Committed in:** 45e99fd (Task 2 commit)

**4. [Rule 1 - Bug] Added asyncio_default_test_loop_scope = session to pyproject.toml**
- **Found during:** Task 2 (diagnosing double-instantiation)
- **Issue:** Without this setting, test functions ran in function-scoped event loops even though fixtures had session scope, causing re-instantiation on each function's setup
- **Fix:** Added `asyncio_default_test_loop_scope = "session"` to [tool.pytest.ini_options]
- **Files modified:** pyproject.toml
- **Verification:** All session-scoped fixtures instantiated exactly once
- **Committed in:** 45e99fd (Task 2 commit)

---

**Total deviations:** 4 auto-fixed (2 bug fixes, 2 blocking issues)
**Impact on plan:** All auto-fixes necessary for correct operation in this container environment. No scope creep — the test infrastructure works exactly as the plan specified.

## Issues Encountered
- pytest-asyncio 1.x breaking change in session fixture handling required pinning to 0.x series. The plan specified `>=0.23` without an upper bound; environment resolved to 1.3.0 which had incompatible session scope behavior. Pinning to `<1.0` resolved immediately.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Test infrastructure complete and verified: UvicornTestServer starts/stops cleanly, socketio.AsyncClient connects, SocketIO event round-trips work
- Phase 3 (Deepgram SDK integration) can write TDD tests using the sio_client fixture
- Known benign artifact: "Task was destroyed but it is pending" from asyncio after session teardown (SocketIO ping task) — does not affect test results or process exit code
- Blockers carried forward: Deepgram 10-second idle timeout validation, sio.session(sid) concurrent access, request_id timing relative to stream_started

## Self-Check: PASSED

- tests/conftest.py: FOUND
- tests/test_app.py: FOUND
- tests/test_client.py: deleted (correct)
- 02-01-SUMMARY.md: FOUND
- commit 2ef33a6: FOUND
- commit 45e99fd: FOUND
- `uv run pytest tests/ -v`: 15 passed, 1 skipped, 0 errors

---
*Phase: 02-async-test-infrastructure*
*Completed: 2026-03-05*
