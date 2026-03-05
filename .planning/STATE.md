# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-05)

**Core value:** Accurate, real-time browser mic transcription demonstrating the official Deepgram Python SDK pattern end-to-end
**Current focus:** Phase 1 — Eliminate gevent + ASGI Skeleton

## Current Position

Phase: 1 of 5 (Eliminate gevent + ASGI Skeleton)
Plan: 0 of TBD in current phase
Status: Ready to plan
Last activity: 2026-03-05 — Milestone v2.0 roadmap created (5 phases, 14 requirements mapped)

Progress: [░░░░░░░░░░] 0%

## Performance Metrics

**Velocity:**
- Total plans completed: 0
- Average duration: -
- Total execution time: 0 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| - | - | - | - |

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [Pre-v2]: gevent monkey.patch_all() must be deleted as the VERY FIRST edit — corrupts asyncio event loop at import time
- [Pre-v2]: uvicorn must serve socketio.ASGIApp (not fastapi_app) — pointing at FastAPI causes SocketIO 404s
- [Pre-v2]: AsyncServer has no test_client() — tests require real UvicornTestServer + socketio.AsyncClient fixture
- [Pre-v2]: SDK callbacks must be `async def` with `**kwargs` — sync or missing-kwargs callbacks silently never fire
- [v1.0]: All 36 tests passing on branch `feature/alpine-frontend-stt-refactor` before v2 work begins

### Pending Todos

None yet.

### Blockers/Concerns

- [Phase 3]: Deepgram 10-second idle timeout is estimated, not formally documented — validate during Phase 3 integration testing
- [Phase 3]: sio.session(sid) vs raw dict for per-session state — validate concurrent access behavior early
- [Phase 3]: request_id from ListenV1Metadata timing relative to stream_started emission — needs integration validation

## Session Continuity

Last session: 2026-03-05
Stopped at: Roadmap created, STATE.md initialized — ready to plan Phase 1
Resume file: None
