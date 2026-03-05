# State: deepgram-python-stt

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-05)

**Core value:** Real-time browser mic → Deepgram SDK → live transcript, as a reference implementation
**Current focus:** Milestone v2.0 — SDK Migration

## Current Position

Phase: Not started (defining requirements)
Plan: —
Status: Defining requirements
Last activity: 2026-03-05 — Milestone v2.0 started

## Accumulated Context

- v1.0 shipped: Flask + websocket-client streaming, Alpine.js frontend, batch transcription, debug panel
- All 36 tests passing on branch `feature/alpine-frontend-stt-refactor`
- Deployed at https://deepgram-python-stt.fly.dev/
- deepgram-sdk 6.0.1 already in pyproject.toml but unused for streaming
- Key constraint: keep SocketIO event names identical so frontend needs zero changes
