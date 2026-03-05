---
gsd_state_version: 1.0
milestone: v2.0
milestone_name: SDK Migration
status: unknown
last_updated: "2026-03-05T19:16:31Z"
progress:
  total_phases: 5
  completed_phases: 4
  total_plans: 8
  completed_plans: 7
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-05)

**Core value:** Accurate, real-time browser mic transcription demonstrating the official Deepgram Python SDK pattern end-to-end
**Current focus:** Phase 4 complete — file streaming and batch transcription both implemented. Ready for Phase 5.

## Current Position

Phase: 4 of 5 (File Streaming + Batch) — COMPLETE
Plan: 2 of 2 in current phase — COMPLETE
Status: Phase 4 complete — file streaming (WebSocket) and batch transcription (REST/httpx) both implemented
Last activity: 2026-03-05 — Completed plan 04-02: /transcribe route implemented with httpx.AsyncClient, 28 tests pass

Progress: [█████████░] 85%

## Performance Metrics

**Velocity:**
- Total plans completed: 5
- Average duration: 3.6 min
- Total execution time: ~18 min

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01-eliminate-gevent-asgi-skeleton | 2 | 7 min | 3.5 min |
| 02-async-test-infrastructure | 1 | 5 min | 5 min |
| 03-deepgram-sdk-streaming | 1 | 4 min | 4 min |
| 04-file-streaming-batch | 2 | 10 min | 5 min |

*Updated after each plan completion*
| Phase 03-deepgram-sdk-streaming P02 | 30 | 2 tasks | 1 files |
| Phase 04-file-streaming-batch P01 | 8 | 2 tasks | 2 files |
| Phase 04-file-streaming-batch P02 | 2 | 2 tasks | 2 files |

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [Pre-v2]: gevent monkey.patch_all() must be deleted as the VERY FIRST edit — corrupts asyncio event loop at import time
- [Pre-v2]: uvicorn must serve socketio.ASGIApp (not fastapi_app) — pointing at FastAPI causes SocketIO 404s
- [Pre-v2]: AsyncServer has no test_client() — tests require real UvicornTestServer + socketio.AsyncClient fixture
- [Pre-v2]: SDK callbacks must be `async def` with `**kwargs` — sync or missing-kwargs callbacks silently never fire
- [v1.0]: All 36 tests passing on branch `feature/alpine-frontend-stt-refactor` before v2 work begins
- [01-01]: uvicorn Dockerfile CMD must target app:app (socketio.ASGIApp), not app:fastapi_app — confirmed in plan execution
- [01-01]: python-socketio 5.14.3 asyncio extra warning is benign — async support is built-in, not gated by extra
- [01-02]: python-multipart must be in dependencies — FastAPI raises RuntimeError at route registration (not request time) when UploadFile is used without it
- [01-02]: All SocketIO stubs emit expected lifecycle events (stream_started/stream_finished) so frontend does not hang waiting for responses
- [Phase 02-01]: Test server port changed from 8765 to 9765 — port 8765 is occupied by node process in container environment
- [Phase 02-01]: pytest-asyncio pinned to <1.0 — v1.3.0 double-instantiates session fixtures causing OSError on port bind
- [Phase 02-01]: aiohttp required as dev dep — socketio.AsyncClient needs it for WebSocket transport
- [Phase 02-01]: asyncio_default_test_loop_scope=session required alongside fixture_loop_scope to prevent re-instantiation of session fixtures in function loops
- [Phase 03-01]: AsyncDeepgramClient(api_key=api_key) — constructor takes keyword-only args (Fern-generated BaseClient uses * in signature); positional arg raises TypeError
- [Phase 03-01]: Integration tests with test-key receive Deepgram 401; streaming_task catches exception and emits stream_finished via finally block; tests accept stream_started OR stream_finished
- [Phase 03-01]: test_no_threading_in_app checks import threading / threading.Thread / threading.Event explicitly to avoid false positive on async_mode comment
- [Phase 03-02]: Audio timeslice must be 250ms (not 1000ms) — larger chunks cause tail words to be dropped when Stop is clicked before last chunk flushes
- [Phase 03-02]: stream_started must emit immediately on WS connect, not after awaiting Metadata — Metadata timing is non-deterministic and can block frontend for 10s+
- [Phase 03-02]: Deepgram API boolean params must be lowercase JSON (true/false), not Python bool (True/False) — Python bools silently ignored or cause key mismatch
- [Phase 04-01]: file_streaming_task has no keep-alive loop — file streaming completes in seconds, unlike mic streaming which runs indefinitely
- [Phase 04-01]: FileNotFoundError emits stream_error then closes WS gracefully (send_close_stream + await listen_task), not an early return without cleanup
- [Phase 04-01]: on_stop_file_streaming signature (sid, data=None) — optional data param to handle frontend sending payload
- [Phase 04-02]: httpx used directly in app.py (import httpx added) — was previously only in tests; FILE-02 requires no requests library
- [Phase 04-02]: Boolean params sent as lowercase strings ("true"/"false") in query_params to Deepgram REST API — same pattern as WebSocket SDK kwargs
- [Phase 04-02]: test_transcribe_url_source_returns_non_501 makes real outbound HTTP call — validates wiring, accepts 401 (test-key) as valid non-stub behavior

### Pending Todos

None yet.

### Blockers/Concerns

- [Phase 3]: Deepgram 10-second idle timeout is estimated, not formally documented — keep-alive validated in live testing, but formal docs not found

## Session Continuity

Last session: 2026-03-05
Stopped at: Completed 04-02-PLAN.md — FILE-02 implemented, 28 tests passing, Phase 4 complete
Resume file: None
