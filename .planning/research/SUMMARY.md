# Project Research Summary

**Project:** deepgram-python-stt v2.0 — SDK Migration
**Domain:** Browser-based live speech-to-text app — Flask/gevent/websocket-client to FastAPI/asyncio/deepgram-sdk
**Researched:** 2026-03-05
**Confidence:** HIGH

## Executive Summary

This migration replaces a synchronous, gevent-patched Flask stack with a fully asyncio-native stack built on FastAPI, python-socketio, and the deepgram-sdk 6.0.1 async streaming API. The root cause driving the rewrite is a fundamental incompatibility: the current `stt/client.py` bypasses the deepgram-sdk entirely, using raw `websocket-client` + `threading.Thread` patterns that rely on gevent monkey-patching to fake async behavior. deepgram-sdk 6.0.1 uses `websockets` natively over asyncio, making gevent incompatible. The recommended approach is to eliminate gevent completely and rebuild on `socketio.AsyncServer(async_mode="asgi")` wrapped around a FastAPI app, served by uvicorn.

The good news is that the scope of change is narrower than a full rewrite. The Alpine.js frontend is untouched — all existing SocketIO event names (`toggle_transcription`, `audio_stream`, `transcription_update`, etc.) and payload shapes are preserved as a hard constraint. The `stt/options.py` module, `common/audio_settings.py`, and all static assets remain unchanged. The migration touches `app.py` (full rewrite), `stt/client.py` (replaced by `stt/session.py` + `stt/deepgram_client.py`), `pyproject.toml` (dependency swap), and the Dockerfile CMD. The SDK's async streaming API (`AsyncDeepgramClient.listen.v1.connect()`) maps cleanly onto the current callback architecture — the main complexity is threading vs. asyncio task management and correct per-session state design.

The biggest implementation risks are (1) the test suite requiring a complete rewrite — python-socketio's `AsyncServer` has no `test_client` equivalent, requiring a real `UvicornTestServer` + `AsyncClient` fixture approach, and (2) subtle event-loop blocking if any `threading.Event`, `time.sleep()`, or sync `requests` calls carry over from v1. Both risks are well-documented and have clear mitigations. If test infrastructure is established first and the threading audit is done before any other code, the migration has no architectural unknowns.

---

## Key Findings

### Recommended Stack

The v1 stack (Flask + Flask-SocketIO + gevent + websocket-client + gunicorn) must be entirely removed. Every component has a direct async replacement that is well-documented and production-tested. The deepgram-sdk 6.0.1 is already installed and pinned — no SDK version upgrade is needed, only correct use of the async client path it already exposes.

**Core technologies:**

- `fastapi>=0.115.0,<1` — ASGI web framework for HTTP routes; used as the `other_asgi_app` that python-socketio wraps
- `python-socketio>=5.11.0,<6` — `AsyncServer(async_mode="asgi")` replaces Flask-SocketIO; mounted via `socketio.ASGIApp`
- `uvicorn>=0.30.0,<1` — ASGI server replaces gunicorn; run with `--workers 1` (SocketIO state is not cross-process)
- `httpx>=0.27.0,<1` — async HTTP client replaces `requests` for batch transcription; already a transitive dep of deepgram-sdk
- `deepgram-sdk==6.0.1` (keep, pin exactly) — use `AsyncDeepgramClient.listen.v1.connect()` async context manager

**Remove:** `flask`, `flask-socketio`, `gevent`, `gevent-websocket`, `gunicorn`, `websocket-client`, `requests`

**Critical uvicorn constraint:** The object uvicorn serves must be `socketio.ASGIApp`, not the `fastapi_app`. Pointing uvicorn at `fastapi_app` causes all Socket.IO connections to 404.

See `.planning/research/STACK.md` for full pyproject.toml dependency block and SDK API reference.

### Expected Features

The migration must preserve all existing functionality exactly — the frontend is not changing. New SDK capabilities are available as low-complexity additions once the migration is complete.

**Must have (table stakes — migration must not regress these):**

- Open streaming WebSocket via `async with client.listen.v1.connect(model=..., ...)` — replaces manual URL + `create_connection()`
- Send audio chunks via `await socket.send_media(bytes)` — replaces `ws.send_binary()`
- Send CloseStream via `await socket.send_close_stream()` — replaces manual JSON send
- Receive interim and final transcripts via `EventType.MESSAGE` with typed `ListenV1Results` — replaces raw JSON dict parsing
- Connection error and close detection via `EventType.ERROR` / `EventType.CLOSE` callbacks
- Batch transcription via `httpx.AsyncClient` or SDK media client — replaces `requests.post()`
- All existing SocketIO event names and payload shapes preserved verbatim

**Should have (new SDK capabilities, low complexity):**

- `send_keep_alive()` — prevents idle timeout during speech pauses (currently absent, causes silent disconnects after ~10s)
- `send_finalize()` — flushes Deepgram's audio buffer; fixes final-words-dropped issue in file streaming
- `request_id` from `ListenV1Metadata` — can surface real request ID in `stream_started` event instead of `None`
- `UtteranceEnd` events — explicit utterance boundary signal, better than inferring from `speech_final`
- `SpeechStarted` events — VAD-based speech detection, currently silently ignored

**Defer (out of scope for this migration):**

- v2 Listen API (`listen.v2`) — different protocol for conversational agents, not live transcription
- Any frontend/Alpine.js changes
- Multi-worker deployment (single worker constraint is correct for this use case)

See `.planning/research/FEATURES.md` for complete SDK API reference and `clean_params()` adaptation pattern.

### Architecture Approach

The architecture follows a three-layer ASGI pattern: `socketio.ASGIApp` at the top wraps `AsyncServer` (handling SocketIO traffic at `/socket.io/`) and `FastAPI` (handling HTTP routes at `/`, `/upload`, `/transcribe`). Per-session Deepgram connections live as `asyncio.Task` objects inside a `streaming_task()` coroutine, replacing the current `threading.Thread` model. Each task owns an `AsyncV1SocketClient` context manager scope, registers async callbacks, and closes cleanly when cancelled. The key insight is that all event handlers, Deepgram callbacks, and SocketIO emits run in the same asyncio event loop — no threads, no locks on the sessions dict (use `sio.session(sid)` for safety).

**Major components:**

1. `app.py` — ASGI wiring (`socketio.ASGIApp` + `FastAPI`), SocketIO event dispatch, HTTP routes; full rewrite
2. `stt/session.py` (new) — `streaming_task()` coroutine that owns one Deepgram WebSocket per browser session; per-session state dataclass
3. `stt/deepgram_client.py` (new, replaces `stt/client.py`) — `AsyncDeepgramClient` singleton factory + connect helper
4. `stt/options.py` (unchanged) — `clean_params()` and `Mode` enum; add str coercion helper for SDK kwargs
5. `common/audio_settings.py` (unchanged) — sync CPU-bound function, safe to call from async handler

**Recommended stop sequence:** `send_close_stream()` → `asyncio.sleep(0.5)` → `task.cancel()` → `await task` (catch `CancelledError`). This ensures Deepgram has time to send final results before the task is forcibly cancelled.

See `.planning/research/ARCHITECTURE.md` for full `streaming_task()` implementation pattern and data flow diagram.

### Critical Pitfalls

1. **No AsyncServer test_client** — python-socketio's `AsyncServer` has no `test_client()`. Every existing SocketIO test must be fully rewritten using a `UvicornTestServer` fixture + `socketio.AsyncClient()`. Attempting to adapt the existing tests will fail silently or with `AttributeError`. Establish the test infrastructure as its own phase before touching app logic.

2. **Threading primitives block the asyncio event loop** — Any `threading.Event.wait()`, `threading.Thread`, or `time.sleep()` that carries over from v1 will silently freeze the uvicorn event loop. SocketIO emits will appear to succeed but never deliver. Audit and replace all threading imports: `threading.Event` → `asyncio.Event`, `threading.Thread` → `asyncio.create_task`, `time.sleep(N)` → `await asyncio.sleep(N)`.

3. **gevent monkey.patch_all() must be deleted first** — The current `app.py` calls `monkey.patch_all()` at import time. If this fires in any code path under uvicorn/asyncio (including test imports), it corrupts the asyncio event loop. Delete these lines as the very first edit before any other migration work.

4. **SDK callbacks require async def and correct signature** — Deepgram SDK callbacks that are `sync def` or missing `**kwargs` are silently never called. No exception is raised. Validate callback receipt with a standalone asyncio script before integrating SocketIO (see Pitfall 3 in PITFALLS.md for exact signature).

5. **Premature connection close drops final words** — Calling `send_close_stream()` and immediately closing the connection causes the last 1-3 seconds of audio to return only interim results. Always wait for a `SpeechFinal` / `UtteranceEnd` event or add `await asyncio.sleep(1.5)` before task cancellation.

See `.planning/research/PITFALLS.md` for 14 pitfalls with detection patterns and phase-specific warnings table.

---

## Implications for Roadmap

Based on the research, the migration decomposes into five phases ordered by dependency chain and risk reduction. The test infrastructure and gevent removal must come first because every subsequent phase depends on them. The SDK integration has hard prerequisites (clean asyncio environment, test harness) that make it impossible to validate without them.

### Phase 1: Eliminate gevent — ASGI Skeleton

**Rationale:** gevent's `monkey.patch_all()` is an import-time bomb that breaks every subsequent async implementation. It must be removed before anything else can be tested. The ASGI wiring skeleton validates that FastAPI + python-socketio + uvicorn work together before SDK complexity is introduced.

**Delivers:** A running ASGI app that serves the existing frontend and accepts SocketIO connections with stubbed handlers. No Deepgram integration yet.

**Addresses:** All table-stakes SocketIO event names preserved; static file serving via FastAPI/Starlette's `StaticFiles`

**Avoids:** Pitfall 6 (gevent monkey-patch corrupts asyncio), Pitfall 7 (wrong `async_mode`), Pitfall 12 (ASGI mount order)

**Stack:** `fastapi`, `python-socketio[asyncio]`, `uvicorn` added; `flask`, `flask-socketio`, `gevent`, `gunicorn` removed

### Phase 2: Test Infrastructure

**Rationale:** The existing test suite will fail completely against the new AsyncServer. Establishing a working `UvicornTestServer` + `AsyncClient` fixture before implementing real functionality means each subsequent phase can be validated immediately via tests. Building tests after implementation creates a false sense of progress.

**Delivers:** Working `conftest.py` with `UvicornTestServer` fixture, `socketio.AsyncClient` fixtures, `pytest-asyncio` in `asyncio_mode="auto"`, and at least one passing SocketIO connect test.

**Avoids:** Pitfall 1 (no AsyncServer test_client), Pitfall 10 (event loop scope mismatch causes test-2+ failures)

**Research flag:** SKIP — pattern is well-documented in python-socketio issues and pytest-asyncio docs. No additional research needed.

### Phase 3: Deepgram SDK Streaming Integration

**Rationale:** This is the core of the migration. With gevent gone and tests working, the `streaming_task()` pattern can be implemented and validated end-to-end. Per-session state, asyncio task lifecycle, and SDK callback wiring are all addressed here.

**Delivers:** Live mic transcription working via `AsyncDeepgramClient.listen.v1.connect()`. All existing `transcription_update` SocketIO events fire correctly. `toggle_transcription` start/stop with clean task cancellation.

**Addresses:** All streaming table-stakes features; `send_keep_alive()` added to prevent idle timeouts

**Uses:** `stt/session.py` (new), `stt/deepgram_client.py` (new), `stt/options.py` (adapted for str coercion)

**Avoids:** Pitfall 2 (threading blocks event loop), Pitfall 3 (SDK callback signature), Pitfall 4 (sid capture at handler time), Pitfall 5 (premature close), Pitfall 8 (KeepAlive), Pitfall 9 (session dict race conditions)

**Research flag:** SKIP — SDK API verified directly from installed source. Architecture pattern fully specified in ARCHITECTURE.md.

### Phase 4: File Streaming and Batch Transcription

**Rationale:** File streaming shares the same `streaming_task()` infrastructure as mic streaming but adds `asyncio.sleep(0.02)` pacing in a task. Batch transcription replaces `requests.post()` with `httpx.AsyncClient`. These are straightforward once Phase 3 is working.

**Delivers:** File upload + streaming transcription working; `/transcribe` batch route using async HTTP; `send_finalize()` implemented to fix final-words-dropped issue.

**Avoids:** Pitfall 2 (`time.sleep()` in file pacing must be `asyncio.sleep()`), Pitfall 5 (premature close — `send_finalize()` + graceful shutdown), Pitfall 14 (sync routes blocking threadpool)

**Research flag:** SKIP — same patterns as Phase 3; httpx is a standard async HTTP client.

### Phase 5: Test Suite Completion and Deployment

**Rationale:** With all features working, the test suite needs full coverage of the new event handlers. Dockerfile CMD update from gunicorn to uvicorn, and Fly.io single-machine constraint documented. This phase closes out the migration.

**Delivers:** Full test coverage on async event handlers; Dockerfile updated; deployment verified on Fly.io

**Avoids:** Pitfall 13 (AsyncClient disconnect hang in test teardown), Gotcha 4/5 from STACK.md (single worker constraint, CORS config)

**Research flag:** SKIP — standard patterns.

### Phase Ordering Rationale

- Phase 1 before Phase 3: gevent removal is a prerequisite — asyncio cannot function with monkey-patching active
- Phase 2 before Phase 3: SDK integration is complex enough that immediate test feedback is critical; writing tests after means bugs go undetected
- Phase 3 before Phase 4: File streaming reuses Phase 3's `streaming_task()` infrastructure
- Phase 5 last: Deployment and test completion are natural finishers after all features work

### Research Flags

All phases use well-documented patterns verified against authoritative sources. No phase requires a `research-phase` planning step.

- **Phase 1:** Standard FastAPI + python-socketio ASGI pattern; official example exists in python-socketio repo
- **Phase 2:** Standard pytest-asyncio + uvicorn test server pattern; documented in multiple python-socketio issues
- **Phase 3:** SDK API verified from installed source; architecture fully specified in ARCHITECTURE.md
- **Phase 4:** httpx and asyncio.sleep patterns are standard
- **Phase 5:** Standard deployment patterns

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | deepgram-sdk 6.0.1 inspected directly from installed source; python-socketio, FastAPI, uvicorn versions verified on PyPI; official examples confirmed |
| Features | HIGH | All features sourced from installed SDK source (`listen/v1/socket_client.py`, `listen/v1/client.py`, `listen/v1/types/`); current v1 behavior sourced from repo code |
| Architecture | HIGH | Patterns verified against python-socketio official docs and official FastAPI integration example; asyncio task lifecycle verified from SDK `core/events.py` |
| Pitfalls | HIGH | All critical pitfalls sourced from verified GitHub issues with confirmed reproduction; gevent/asyncio conflict is a known, documented failure mode |

**Overall confidence:** HIGH

### Gaps to Address

- **Deepgram KeepAlive behavior:** The 10-second idle timeout is documented in GitHub issues but the exact Deepgram-side timeout value should be confirmed during Phase 3 integration testing. The `send_keep_alive()` implementation in `AsyncV1SocketClient` is confirmed; the timeout window is an estimate.
- **`sio.session(sid)` vs raw dict:** PITFALLS.md recommends `sio.session(sid)` context manager for session state. The python-socketio async session API behavior under concurrent access (Pitfall 9) should be validated early in Phase 3 before the sessions dict pattern is widely used.
- **`request_id` from `ListenV1Metadata`:** The `stream_started` event currently emits `request_id: None`. The Metadata event fires first from `start_listening()` but the timing relative to `stream_started` emission needs to be validated — it may require storing the `request_id` from `on_message` and using it only on the first `Results` event, or emitting `stream_started` from the Metadata callback instead.

---

## Sources

### Primary (HIGH confidence)

- Installed deepgram-sdk v6.0.1 source: `/coding/deepgram-python-stt/.venv/lib/python3.12/site-packages/deepgram/` — SDK async API, event types, response models
- [python-socketio official docs](https://python-socketio.readthedocs.io/en/latest/server.html) — AsyncServer, ASGIApp, async_mode
- [python-socketio FastAPI official example](https://github.com/miguelgrinberg/python-socketio/blob/main/examples/server/asgi/fastapi-fiddle.py) — ASGI mount pattern
- Existing `app.py` and `stt/client.py` in this repo — v1 behavior baseline

### Secondary (MEDIUM confidence)

- [python-socketio Discussion #1093](https://github.com/miguelgrinberg/python-socketio/discussions/1093) — confirms threading.Event blocks asyncio loop; community-confirmed
- [deepgram-python-sdk Issue #442](https://github.com/deepgram/deepgram-python-sdk/issues/442) — callback signature requirement (`self, result, **kwargs`)
- [deepgram-python-sdk Issue #361](https://github.com/deepgram/deepgram-python-sdk/issues/361) — keepalive requirement, audio encoding match
- [pytest-asyncio Issue #868](https://github.com/pytest-dev/pytest-asyncio/issues/868) — event loop scope issues

### Tertiary (LOW confidence)

- Deepgram 10-second idle timeout — referenced in GitHub issues, not formally documented; validate during Phase 3 integration testing

---

*Research completed: 2026-03-05*
*Ready for roadmap: yes*
