---
phase: 04-file-streaming-batch
plan: 02
subsystem: api
tags: [httpx, deepgram, batch-transcription, rest-api, async]

# Dependency graph
requires:
  - phase: 04-01
    provides: file_streaming_task and upload route that this batch route complements
  - phase: 03-01
    provides: clean_params, Mode enum used for query param building
provides:
  - POST /transcribe route using httpx.AsyncClient to call Deepgram REST API
  - Supports both URL source (JSON body) and uploaded file source (binary)
  - Boolean params sent as lowercase strings to Deepgram
affects: [phase 05, frontend-integration]

# Tech tracking
tech-stack:
  added: [httpx (now imported in app.py, was only in tests)]
  patterns: [batch REST call via httpx.AsyncClient with 300s timeout, HTTPStatusError propagation]

key-files:
  created: []
  modified:
    - app.py
    - tests/test_app.py

key-decisions:
  - "httpx.AsyncClient used directly in app.py (not requests) — FILE-02 requirement"
  - "Boolean params converted to lowercase strings (true/false) in query_params before sending to Deepgram"
  - "HTTPStatusError propagated with upstream status code (e.g., 401 from Deepgram with test-key)"
  - "test_transcribe_url_source_returns_non_501 makes real outbound HTTP call — validates wiring, not transcription quality"

patterns-established:
  - "Batch transcription: clean_params(params, Mode.BATCH) then bool-to-string conversion then setdefault model"
  - "File source check: file_path.exists() before reading bytes, 404 if not found"
  - "Error handling: httpx.HTTPStatusError → propagate status code; Exception → 500"

requirements-completed: [FILE-02]

# Metrics
duration: 2min
completed: 2026-03-05
---

# Phase 4 Plan 02: Batch /transcribe Route Summary

**POST /transcribe route replaced from 501 stub to real httpx.AsyncClient implementation calling Deepgram REST API with URL and file source support**

## Performance

- **Duration:** 2 min
- **Started:** 2026-03-05T19:14:31Z
- **Completed:** 2026-03-05T19:16:31Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- Replaced 501 stub in `/transcribe` with real httpx.AsyncClient implementation
- Route supports both URL source (JSON body `{"url": ...}`) and uploaded file source (`{"filename": ...}`)
- Boolean query params converted to lowercase strings per Deepgram API requirements
- HTTPStatusError (e.g., 401 with test-key) propagated with upstream status code
- 400 returned immediately for missing source (no Deepgram call made)
- 404 returned for missing uploaded file
- test_transcribe_returns_501 removed; two new tests added covering real behavior
- All 28 tests pass, 1 skipped (no regressions)

## Task Commits

Each task was committed atomically:

1. **Task 1: Implement real /transcribe route with httpx.AsyncClient** - `2f5bed3` (feat)
2. **Task 2: Update test_transcribe test and run full suite** - `251ade3` (test)

**Plan metadata:** (docs commit follows)

## Files Created/Modified
- `/coding/deepgram-python-stt/app.py` - Added `import httpx`; replaced 501 stub with full implementation
- `/coding/deepgram-python-stt/tests/test_app.py` - Replaced test_transcribe_returns_501 with two new tests

## Decisions Made
- `httpx.AsyncClient` timeout set to 300.0 seconds (sufficient for large audio files)
- URL source uses `Content-Type: application/json` with JSON body `{"url": url}`
- File source uses `Content-Type: audio/*` with raw bytes as content body
- Both path branches share the same query_params building logic (clean_params + bool conversion)
- test_transcribe_url_source_returns_non_501 makes real outbound HTTP to Deepgram — with test-key it returns 401, which the test accepts as valid (non-501) behavior

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- FILE-02 complete: batch /transcribe route implemented with httpx.AsyncClient
- Phase 4 is complete (both plans 04-01 and 04-02 done)
- Phase 5 can proceed — all file streaming and batch transcription routes are functional

---
*Phase: 04-file-streaming-batch*
*Completed: 2026-03-05*
