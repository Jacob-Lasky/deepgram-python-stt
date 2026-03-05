---
phase: 03-deepgram-sdk-streaming
plan: "02"
subsystem: testing
tags: [deepgram, streaming, browser, microphone, socketio, live-verification]

# Dependency graph
requires:
  - phase: 03-01
    provides: AsyncDeepgramClient streaming implementation, keep-alive, graceful stop
provides:
  - Live end-to-end verification of Deepgram SDK streaming with real browser mic
  - Confirmed STR-01 through STR-04 with real Deepgram API key
affects: [04-cleanup-polish]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "timeslice 250ms for audio chunks — 1000ms caused dropped words on stop"
    - "stream_started emitted immediately on connect, not after awaiting Metadata event"
    - "Deepgram API boolean params must be lowercase (true/false not True/False)"

key-files:
  created: []
  modified:
    - app.py

key-decisions:
  - "timeslice reduced from 1000ms to 250ms — shorter chunks prevent final words from being dropped when Stop is clicked before the last chunk is sent"
  - "stream_started emitted immediately on Deepgram WS connect, not gated on 10s Metadata wait — Metadata timing is non-deterministic and the frontend must not hang"
  - "Deepgram API params must be lowercase booleans (true/false) — uppercase True/False are silently ignored or cause param key mismatch"

patterns-established:
  - "Audio chunk timeslice: 250ms is the correct value for responsive stop behavior"
  - "Lifecycle events: emit stream_started immediately, emit stream_finished only after listen_task completes"

requirements-completed: [STR-01, STR-02, STR-03, STR-04]

# Metrics
duration: ~30min (includes live browser testing session)
completed: 2026-03-05
---

# Phase 3 Plan 02: Human Verification Summary

**Live browser verification confirmed: real speech produces real Deepgram transcripts, keep-alive sustains 15s silences, stop flushes final words, and start/stop/start cycles cleanly — three bugs found and fixed during testing.**

## Performance

- **Duration:** ~30 min (live browser testing session)
- **Started:** 2026-03-05
- **Completed:** 2026-03-05
- **Tasks:** 2 (server startup + human verification checkpoint)
- **Files modified:** 1 (app.py via three bug fix commits)

## Accomplishments

- All four STR requirements verified with real Deepgram API key and real browser microphone
- Three blocking bugs discovered and fixed during live testing before final approval
- Server startup confirmed clean (no import errors, no forbidden threading patterns)
- Start/stop/start cycle confirmed: no orphaned tasks, no doubled transcription_update events

## Task Commits

1. **Task 1: Start dev server and confirm clean startup** - `93495ff` (baseline from 03-01 completion)
2. **Bug fix: lowercase boolean params** - `a4f8acb` (fix)
3. **Bug fix: params key mismatch + timeslice 1000ms→250ms** - `b1d6367` (fix)
4. **Bug fix: emit stream_started immediately, don't gate on Metadata** - `018ff8b` (fix)

## Files Created/Modified

- `/coding/deepgram-python-stt/app.py` - Three bug fixes applied during live browser verification

## Decisions Made

- timeslice reduced 1000ms→250ms: at 1000ms, the final audio chunk may not have been sent when Stop is clicked, causing tail words to be dropped. 250ms ensures the in-flight chunk is small enough to flush before close_stream.
- stream_started emitted immediately: the original code waited for Deepgram's Metadata event (can take 10+ seconds) before emitting stream_started to the frontend. The frontend was hanging. Fix: emit immediately on WS connect.
- Deepgram params must be lowercase booleans: `diarize: True` was being sent as a Python bool (uppercase), which Deepgram silently ignored. Corrected to `"true"` string or JSON-serialized lowercase.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Deepgram API boolean params sent as Python True/False**
- **Found during:** Live browser verification (Task 2)
- **Issue:** Params like `smart_format: True` serialized to uppercase, silently rejected by Deepgram
- **Fix:** Changed all boolean params to lowercase strings compatible with JSON API
- **Files modified:** app.py
- **Verification:** Live transcription produced correct results after fix
- **Committed in:** `a4f8acb`

**2. [Rule 1 - Bug] params key mismatch + audio timeslice too large (1000ms)**
- **Found during:** Live browser verification (Task 2)
- **Issue:** params dict key referenced wrong field name causing mismatch; 1000ms timeslice caused final words to be dropped when Stop clicked
- **Fix:** Corrected params key; reduced timeslice to 250ms
- **Files modified:** app.py
- **Verification:** Final words ("apple") appeared in transcript before stream_finished
- **Committed in:** `b1d6367`

**3. [Rule 1 - Bug] stream_started gated on 10s Metadata event**
- **Found during:** Live browser verification (Task 2)
- **Issue:** Frontend waited up to 10+ seconds after clicking Start before stream_started fired; UX blocked
- **Fix:** Emit stream_started immediately on WebSocket connect to Deepgram, before any Metadata arrives
- **Files modified:** app.py
- **Verification:** stream_started fires within ~1s of clicking Start
- **Committed in:** `018ff8b`

---

**Total deviations:** 3 auto-fixed (all Rule 1 — bugs)
**Impact on plan:** All three fixes were required for STR criteria to actually pass. No scope creep.

## Issues Encountered

All issues were discovered during the human verification live session and fixed before the user approved. The plan's purpose (live browser validation) worked exactly as intended — surfacing real API integration bugs that unit tests with mock keys could not catch.

## User Setup Required

None - DEEPGRAM_API_KEY was already configured in the environment.

## Next Phase Readiness

- STR-01 through STR-04 are fully verified with a real Deepgram API key
- All three integration bugs fixed and committed
- 24 unit tests still pass (no regressions)
- Phase 4 (cleanup/polish) can begin immediately

---
*Phase: 03-deepgram-sdk-streaming*
*Completed: 2026-03-05*
