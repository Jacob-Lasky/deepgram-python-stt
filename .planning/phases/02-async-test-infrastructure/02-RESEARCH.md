# Phase 2: Async Test Infrastructure - Research

**Researched:** 2026-03-05
**Domain:** pytest-asyncio + python-socketio AsyncClient + UvicornTestServer
**Confidence:** HIGH

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| TEST-01 | Test suite uses `UvicornTestServer` + `socketio.AsyncClient` fixtures (replaces Flask-SocketIO test client) | UvicornTestServer pattern documented below; AsyncClient API confirmed from official docs; pytest-asyncio config required |
</phase_requirements>

---

## Summary

Phase 2 replaces the Flask-SocketIO synchronous `test_client()` pattern with a real ASGI server (Uvicorn) plus `socketio.AsyncClient`. The old tests used `socketio.test_client(app)` which does not exist on `AsyncServer` — the python-socketio maintainer confirmed this gap and the real-server approach is the documented solution.

The primary technical work is: (1) install and configure `pytest-asyncio`, (2) write a `UvicornTestServer` helper class that extends `uvicorn.Server` and exposes `start_up()`/`tear_down()` coroutines, (3) write async pytest fixtures for server and client lifecycle, (4) rewrite the three broken test files so they import cleanly against the new ASGI stack.

The existing tests in `test_app.py` and `test_client.py` fail at collection with `ModuleNotFoundError: No module named 'requests'` because `stt/client.py` still imports `requests` (legacy layer that Phase 3 will replace). Phase 2 must remove or stub those tests entirely — they test the old `STTClient` class which no longer applies.

**Primary recommendation:** Add `pytest-asyncio` as a dev dependency, write `conftest.py` with `UvicornTestServer` + `AsyncClient` fixtures scoped to `session`, configure `asyncio_mode = "auto"` and `asyncio_default_fixture_loop_scope = "session"` in `pyproject.toml`, rewrite `tests/test_app.py` for the ASGI stack, delete or skip `test_client.py` (STTClient is gone in Phase 3).

---

## Current State (Pre-Phase-2)

**Installed versions (confirmed in environment):**
- `uvicorn`: 0.41.0
- `python-socketio`: 5.14.3
- `pytest`: 9.0.2
- `fastapi`: 0.135.1
- `anyio`: 4.11.0
- `pytest-asyncio`: NOT INSTALLED

**Current test failures (confirmed by running `uv run pytest tests/ -v`):**
```
ERROR tests/test_client.py  — ModuleNotFoundError: No module named 'requests'
ERROR tests/test_options.py — ModuleNotFoundError: No module named 'requests'
```
`test_deepgram_sdk.py` is entirely skipped (marked skip at module level). `test_app.py` imports `app.socketio` which no longer exists — it would fail at collection too once the import errors above are fixed.

**Root cause:** `stt/__init__.py` does `from .client import STTClient` and `stt/client.py` imports `requests` (removed in Phase 1). All tests that touch `stt.*` immediately break.

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pytest-asyncio | >=0.23, latest 1.3.0 | Run async test functions and fixtures | Only viable pytest plugin for asyncio; required for `async def` fixtures |
| uvicorn | 0.41.0 (already installed) | Provides `uvicorn.Server` base class for `UvicornTestServer` | Already in stack; real server validates ASGI routing |
| python-socketio | 5.14.3 (already installed) | `socketio.AsyncClient` for client-side test connections | Same library as server; protocol compatibility guaranteed |
| anyio | 4.11.0 (already installed) | Async backend used by pytest-asyncio under the hood | Already present via FastAPI |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| httpx | already installed | Test HTTP routes (index, upload, transcribe) | Use `httpx.AsyncClient` for async HTTP assertions |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Real UvicornTestServer | Mock/in-process ASGI test transport | python-socketio AsyncServer has no test_client(); real server is mandatory |
| pytest-asyncio | anyio pytest plugin | anyio is already installed but pytest-asyncio integrates better with session-scoped fixtures |

**Installation (dev dependency):**
```bash
uv add --dev pytest-asyncio
```

---

## Architecture Patterns

### Recommended Project Structure

The test infrastructure touches these files:

```
tests/
├── conftest.py         # UvicornTestServer class + all async fixtures (NEW)
├── test_app.py         # Rewrite for ASGI stack (HTTP + SocketIO)
├── test_client.py      # DELETE or skip entirely (tests old STTClient)
├── test_options.py     # DELETE or skip entirely (imports via stt.__init__)
└── test_deepgram_sdk.py  # Already fully skipped, leave as-is
pyproject.toml          # Add pytest-asyncio config options
```

### Pattern 1: UvicornTestServer Class

**What:** A subclass of `uvicorn.Server` that exposes async `start_up()` and `tear_down()` methods, using an `asyncio.Event` to signal when startup is complete.

**When to use:** Any time you need to start a real ASGI server inside a test session.

```python
# Source: Haseeb Majid's blog (verified pattern), adapted for this project
import asyncio
from typing import Optional
import uvicorn
from app import app  # the socketio.ASGIApp instance

HOST = "127.0.0.1"
PORT = 8765  # pick a port that won't conflict with dev server (8001)

class UvicornTestServer(uvicorn.Server):
    def __init__(self, application=app, host=HOST, port=PORT):
        self._startup_done = asyncio.Event()
        super().__init__(config=uvicorn.Config(application, host=host, port=port, log_level="error"))

    async def startup(self, sockets=None) -> None:
        """Override: signal startup_done after server is ready."""
        await super().startup(sockets=sockets)
        self._startup_done.set()

    async def start_up(self) -> None:
        """Start server in a background task and wait until ready."""
        self._serve_task = asyncio.create_task(self.serve())
        await self._startup_done.wait()

    async def tear_down(self) -> None:
        """Gracefully shut down the server."""
        self.should_exit = True
        await self._serve_task
```

**Critical note:** The `startup` override uses `sockets=None` keyword argument — this matches uvicorn 0.41.0's signature. Earlier examples online omit the `sockets` parameter and may fail.

### Pattern 2: Pytest Fixtures (Session-Scoped)

**What:** Async pytest fixtures scoped to `session` so the server starts once for all tests.

**When to use:** All async integration tests in this phase.

```python
# Source: pytest-asyncio docs + python-socketio issue #263 fix
import pytest_asyncio
import socketio

BASE_URL = f"http://{HOST}:{PORT}"

@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def server():
    """Start real ASGI app once for the whole test session."""
    srv = UvicornTestServer()
    await srv.start_up()
    yield
    await srv.tear_down()

@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def sio_client(server):
    """Connected socketio.AsyncClient for the test session."""
    client = socketio.AsyncClient()
    await client.connect(BASE_URL, transports=["websocket"])
    yield client
    await client.disconnect()
    await client.wait()  # CRITICAL: prevents hang on teardown (issue #263)
```

**Critical:** `await client.wait()` after `disconnect()` is required. Without it, background tasks on the server remain live and the test process hangs indefinitely after the suite finishes.

### Pattern 3: pyproject.toml Configuration

```toml
[tool.pytest.ini_options]
pythonpath = ["."]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "session"
```

- `asyncio_mode = "auto"` — eliminates the need to mark every async test with `@pytest.mark.asyncio`
- `asyncio_default_fixture_loop_scope = "session"` — required for session-scoped async fixtures to share the same event loop; without this, pytest-asyncio >=0.23 emits deprecation warnings and may create per-function loops that break session fixtures

### Pattern 4: Future-Based Event Response Assertion

**What:** Receive a SocketIO event from the server by registering a one-shot handler that resolves an `asyncio.Future`.

**When to use:** Any test that needs to assert on a server-emitted event (emit → expect response).

```python
# Source: Haseeb Majid's blog + python-socketio client docs
async def test_toggle_transcription_start_emits_stream_started(sio_client):
    future = asyncio.get_running_loop().create_future()

    @sio_client.on("stream_started")
    def on_stream_started(data):
        if not future.done():
            future.set_result(data)

    await sio_client.emit("toggle_transcription", {"action": "start", "params": {}})
    result = await asyncio.wait_for(future, timeout=5.0)
    assert result["request_id"] is None
```

**Critical:** Guard `future.set_result()` with `if not future.done()` — if the event fires multiple times (e.g., from a previous test), calling `set_result` on an already-resolved future raises `InvalidStateError`.

### Pattern 5: HTTP Route Testing with httpx

**What:** Test FastAPI HTTP routes using `httpx.AsyncClient` with the ASGI app directly (no server required).

**When to use:** Testing `GET /`, `POST /upload`, `POST /transcribe` without spinning up the full SocketIO server.

```python
# Source: FastAPI docs on testing + httpx ASGI transport
from httpx import AsyncClient, ASGITransport
from app import fastapi_app  # the FastAPI sub-app

async def test_index_returns_html():
    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test") as client:
        resp = await client.get("/")
    assert resp.status_code == 200
    assert b"<!DOCTYPE html>" in resp.content or b"<html" in resp.content
```

**Note:** Use `fastapi_app` (not `app`) for HTTP route tests — `app` is the `socketio.ASGIApp` wrapper and routes its traffic through SocketIO first.

### Anti-Patterns to Avoid

- **`socketio.test_client(app)`:** Does not exist on `AsyncServer`. This call raises `AttributeError`. All occurrences in `test_app.py` must be removed.
- **Redefining `event_loop` fixture:** Deprecated in pytest-asyncio >=0.22, removed in 1.0. Use `loop_scope="session"` on `@pytest_asyncio.fixture` instead.
- **`scope="session"` without `loop_scope="session"`:** In pytest-asyncio >=0.23, session-scoped async fixtures require the explicit `loop_scope` to run in the session event loop. Omitting it produces deprecation warnings and intermittent failures.
- **Missing `await client.wait()` in teardown:** Client hangs the entire test runner indefinitely (confirmed in python-socketio issue #263).
- **Using `import socketio` from `app` (old `app.socketio`):** The new `app.py` does not export `socketio` — it exports `sio`. Tests must not import `socketio` from `app`; they create their own `socketio.AsyncClient()`.
- **Importing from `stt.*` in tests:** `stt/__init__.py` transitively imports `requests` which is removed. Any test file that does `from stt.client import STTClient` fails at collection. Phase 2 must either delete these tests or add them to a skip block.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Async test runner | Custom asyncio `run_until_complete` harness | pytest-asyncio | Handles event loop lifecycle, fixture scoping, teardown ordering |
| HTTP route assertions | Raw `httpx` calls against live server | `httpx.AsyncClient(transport=ASGITransport(...))` | No port binding needed; faster, no race conditions |
| Waiting for server ready | Polling `GET /` with retries | `asyncio.Event` in `startup()` override | Server signals exact readiness moment |
| Event response assertions | `asyncio.sleep()` + polling | `asyncio.Future` + `asyncio.wait_for()` | Deterministic; fails fast on timeout |

---

## Common Pitfalls

### Pitfall 1: Test Process Hangs After Suite Completes
**What goes wrong:** After all tests pass, pytest hangs indefinitely and must be killed.
**Why it happens:** `asyncio.AsyncClient` spawns background tasks (ping loop, receive loop) that are still running when the fixture tears down. `disconnect()` initiates but does not await full cleanup.
**How to avoid:** Always `await client.wait()` after `await client.disconnect()` in fixture teardown.
**Warning signs:** `uv run pytest` returns no prompt after "X passed" line.

### Pitfall 2: Session-Scoped Fixtures in Wrong Event Loop
**What goes wrong:** `RuntimeError: Task attached to a different loop` or `ScopeMismatch` errors.
**Why it happens:** pytest-asyncio >=0.23 creates per-function loops by default. Session fixtures run in a different loop from test functions unless configured.
**How to avoid:** Set `asyncio_default_fixture_loop_scope = "session"` in `pyproject.toml` AND use `loop_scope="session"` on `@pytest_asyncio.fixture`.
**Warning signs:** Error mentions "different event loop" or fixture teardown raises `RuntimeError`.

### Pitfall 3: `startup()` Override Signature Mismatch
**What goes wrong:** `TypeError: startup() got an unexpected keyword argument 'sockets'`.
**Why it happens:** uvicorn 0.30+ passes `sockets=None` to `startup()`; older blog post examples define `async def startup(self) -> None:` without the parameter.
**How to avoid:** Define `async def startup(self, sockets=None) -> None:` to accept the argument.
**Warning signs:** Server fixture fails on startup with TypeError.

### Pitfall 4: Port Collision Between Test Server and Dev Server
**What goes wrong:** `OSError: [Errno 98] Address already in use`.
**Why it happens:** Dev server runs on port 8001; if tests use the same port, they collide.
**How to avoid:** Pick a distinct test port (e.g., 8765) in `UvicornTestServer`.
**Warning signs:** Server fixture fails at startup with OSError.

### Pitfall 5: Import-Time Failures from Legacy `stt` Module
**What goes wrong:** `ERROR collecting tests/test_client.py — ModuleNotFoundError: No module named 'requests'`.
**Why it happens:** `stt/__init__.py` eagerly imports `STTClient` from `stt/client.py` which imports `requests` (removed in Phase 1).
**How to avoid:** Delete `tests/test_client.py` and `tests/test_options.py` in Phase 2. These files test the old `STTClient` class that no longer exists in the Phase 1 stack. Their tests will be replaced in Phase 5.
**Warning signs:** Collection errors before any test runs.

### Pitfall 6: Future Already Done on Second Event
**What goes wrong:** `asyncio.InvalidStateError: Result is already set`.
**Why it happens:** The `@sio_client.on(event)` handler registers permanently on the session-scoped client. If the event fires again in a later test, the handler tries to set a result on a future that's already resolved.
**How to avoid:** Guard with `if not future.done(): future.set_result(data)` OR remove the handler after use with `sio_client.handlers.pop(event, None)`.
**Warning signs:** Test passes first time, raises `InvalidStateError` on second run.

---

## Code Examples

### Full conftest.py

```python
# Source: Derived from https://haseebmajid.dev/posts/2021-12-23-testing-a-socketio-web-app-written-in-python/
#         + python-socketio issue #263 teardown fix
#         + pytest-asyncio 0.23+ loop_scope pattern
import asyncio
import pytest
import pytest_asyncio
import socketio
import uvicorn
from app import app  # socketio.ASGIApp instance

HOST = "127.0.0.1"
PORT = 8765
BASE_URL = f"http://{HOST}:{PORT}"


class UvicornTestServer(uvicorn.Server):
    def __init__(self, application=app, host=HOST, port=PORT):
        self._startup_done = asyncio.Event()
        super().__init__(config=uvicorn.Config(
            application, host=host, port=port, log_level="error"
        ))

    async def startup(self, sockets=None) -> None:
        await super().startup(sockets=sockets)
        self._startup_done.set()

    async def start_up(self) -> None:
        self._serve_task = asyncio.create_task(self.serve())
        await self._startup_done.wait()

    async def tear_down(self) -> None:
        self.should_exit = True
        await self._serve_task


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def server():
    srv = UvicornTestServer()
    await srv.start_up()
    yield
    await srv.tear_down()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def sio_client(server):
    client = socketio.AsyncClient()
    await client.connect(BASE_URL, transports=["websocket"])
    yield client
    await client.disconnect()
    await client.wait()
```

### Rewritten test_app.py (async ASGI stack)

```python
# tests/test_app.py
import asyncio
import os
from io import BytesIO

import pytest
from httpx import AsyncClient, ASGITransport

os.environ.setdefault("DEEPGRAM_API_KEY", "test-key")

from app import fastapi_app  # HTTP sub-app only


# ---- HTTP routes (in-process via ASGITransport, no real server needed) ----

async def test_index_returns_html():
    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.get("/")
    assert resp.status_code == 200
    assert b"<!DOCTYPE html>" in resp.content or b"<html" in resp.content


async def test_upload_file():
    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/upload",
            files={"file": ("test.wav", BytesIO(b"fake audio"), "audio/wav")},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["filename"] == "test.wav"
    assert body["size"] > 0


async def test_transcribe_returns_501():
    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.post("/transcribe", json={})
    assert resp.status_code == 501


# ---- SocketIO events (uses session-scoped sio_client from conftest) ----

async def test_socketio_connects(sio_client):
    assert sio_client.connected


async def test_toggle_transcription_start_emits_stream_started(sio_client):
    future = asyncio.get_running_loop().create_future()

    @sio_client.on("stream_started")
    def on_stream_started(data):
        if not future.done():
            future.set_result(data)

    await sio_client.emit("toggle_transcription", {"action": "start", "params": {}})
    result = await asyncio.wait_for(future, timeout=5.0)
    assert "request_id" in result


async def test_toggle_transcription_stop_emits_stream_finished(sio_client):
    future = asyncio.get_running_loop().create_future()

    @sio_client.on("stream_finished")
    def on_stream_finished(data):
        if not future.done():
            future.set_result(data)

    await sio_client.emit("toggle_transcription", {"action": "stop", "params": {}})
    result = await asyncio.wait_for(future, timeout=5.0)
    assert "request_id" in result
```

### pyproject.toml additions

```toml
[tool.uv]
package = false
dev-dependencies = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",  # ADD THIS
]

[tool.pytest.ini_options]
pythonpath = ["."]
asyncio_mode = "auto"                          # ADD THIS
asyncio_default_fixture_loop_scope = "session" # ADD THIS
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `socketio.test_client(app)` | Real `UvicornTestServer` + `socketio.AsyncClient` | Flask-SocketIO → python-socketio AsyncServer | Cannot use sync test_client on AsyncServer; real server required |
| Custom `event_loop` fixture override | `loop_scope="session"` on `@pytest_asyncio.fixture` | pytest-asyncio 0.22 deprecation, removed in 1.0 | Must use `loop_scope` kwarg instead |
| `@pytest.mark.asyncio` on every test | `asyncio_mode = "auto"` in config | pytest-asyncio 0.19+ | Auto mode eliminates per-test decorator |
| `httpx.AsyncClient(app=...)` | `httpx.AsyncClient(transport=ASGITransport(app=...))` | httpx >=0.20 | `app=` param removed; use transport object |

**Deprecated/outdated:**
- `socketio.test_client()`: Not available on `AsyncServer` — maintainer confirmed in issue #332
- Overriding `event_loop` fixture: Removed in pytest-asyncio 1.0 — use `loop_scope` instead
- `httpx.AsyncClient(app=...)` shorthand: Replaced by `ASGITransport` wrapper

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 + pytest-asyncio (to be installed) |
| Config file | `pyproject.toml` — `[tool.pytest.ini_options]` |
| Quick run command | `uv run pytest tests/test_app.py -v` |
| Full suite command | `uv run pytest tests/ -v` |

### Phase Requirements to Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| TEST-01 (a) | pytest runs without import errors | smoke | `uv run pytest tests/ --collect-only` | Wave 0 gap |
| TEST-01 (b) | UvicornTestServer starts and stops | integration | `uv run pytest tests/test_app.py::test_socketio_connects -v` | Wave 0 gap |
| TEST-01 (c) | socketio.AsyncClient connects and receives connect event | integration | `uv run pytest tests/test_app.py::test_socketio_connects -v` | Wave 0 gap |
| TEST-01 (d) | Full round-trip: emit event, receive response | integration | `uv run pytest tests/test_app.py::test_toggle_transcription_start_emits_stream_started -v` | Wave 0 gap |

### Sampling Rate

- **Per task commit:** `uv run pytest tests/test_app.py -v`
- **Per wave merge:** `uv run pytest tests/ -v`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `tests/conftest.py` — `UvicornTestServer` class + `server` + `sio_client` fixtures (does not exist yet)
- [ ] `tests/test_app.py` — complete rewrite for async ASGI stack (current file uses Flask-SocketIO API)
- [ ] `tests/test_client.py` — DELETE (imports `stt.client` which imports removed `requests`)
- [ ] `tests/test_options.py` — DELETE (same import chain failure)
- [ ] Framework install: `uv add --dev pytest-asyncio` — not currently in pyproject.toml
- [ ] pyproject.toml: add `asyncio_mode = "auto"` and `asyncio_default_fixture_loop_scope = "session"`

---

## Open Questions

1. **`startup()` signature in uvicorn 0.41.0**
   - What we know: uvicorn >=0.30 passes `sockets=None` to `startup()`; blog examples omit this
   - What's unclear: Whether uvicorn 0.41.0 uses exactly this signature or has changed further
   - Recommendation: Include `sockets=None` in the override signature; if uvicorn changes it, the error will be explicit at startup

2. **Per-test vs session-scoped sio_client**
   - What we know: Session-scoped client avoids startup overhead; but registered handlers accumulate
   - What's unclear: Whether accumulated handlers cause false positives in later tests
   - Recommendation: Session scope is correct for Phase 2 (minimal test suite); use the `if not future.done()` guard on all response futures

3. **`test_options.py` deletion scope**
   - What we know: `test_options.py` tests `stt.options.clean_params` which is pure logic with no async/Flask dependency
   - What's unclear: Whether `clean_params` survives into the new stack (likely yes, with different callers in Phase 3)
   - Recommendation: Do NOT delete `test_options.py` — fix the import by making `stt/__init__.py` lazy or removing the eager import. These tests are valuable for Phase 3 regression.

---

## Sources

### Primary (HIGH confidence)
- python-socketio official docs — [The Socket.IO Clients](https://python-socketio.readthedocs.io/en/latest/client.html) — AsyncClient API
- pytest-asyncio PyPI — [pypi.org/project/pytest-asyncio](https://pypi.org/project/pytest-asyncio/) — version 1.3.0, config options
- pytest-asyncio docs — [Change fixture loop](https://pytest-asyncio.readthedocs.io/en/latest/how-to-guides/change_fixture_loop.html) — loop_scope pattern

### Secondary (MEDIUM confidence)
- [Haseeb Majid's blog](https://haseebmajid.dev/posts/2021-12-23-testing-a-socketio-web-app-written-in-python/) — UvicornTestServer pattern (2021, core pattern still valid, startup signature needs update)
- [python-socketio issue #263](https://github.com/miguelgrinberg/python-socketio/issues/263) — AsyncClient hang fix: `await client.wait()` after disconnect
- [pytest-asyncio changelog](https://pytest-asyncio.readthedocs.io/en/stable/reference/changelog.html) — event_loop deprecation timeline, loop_scope introduction

### Tertiary (LOW confidence)
- WebSearch results on uvicorn.Server startup override — pattern confirmed by multiple sources but uvicorn 0.41.0 signature not directly verified against source

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — versions confirmed from installed environment; pytest-asyncio is the only viable option
- Architecture: HIGH — UvicornTestServer pattern confirmed by official python-socketio documentation noting no test_client() + blog post; AsyncClient teardown fix confirmed from GitHub issue
- Pitfalls: HIGH — pitfalls confirmed by running the actual test suite and observing failures; loop_scope issue confirmed from official changelog

**Research date:** 2026-03-05
**Valid until:** 2026-06-05 (90 days — pytest-asyncio API is relatively stable; uvicorn startup override signature may change on major version)
