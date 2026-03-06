# Phase 5: Test Coverage + Deployment - Research

**Researched:** 2026-03-05
**Domain:** pytest async testing coverage gaps + Fly.io deployment
**Confidence:** HIGH

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| TEST-02 | All existing test scenarios covered and passing on the new async stack | Coverage gap analysis reveals `detect_audio_settings` handler untested and `audio_stream` lacks live SocketIO emit test; unit test patterns from test_streaming.py are directly reusable |
| DEPL-01 | Dockerfile CMD updated from gunicorn to uvicorn | Already done — Dockerfile line 41 is `CMD ["uv", "run", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]`; no code change needed, only verification |
| DEPL-02 | App deploys and runs successfully on Fly.io (deepgram-python-stt.fly.dev) | fly.toml is fully configured; DEEPGRAM_API_KEY secret must be set; flyctl must be installed in CI/dev environment; `fly deploy` is the deploy command |
</phase_requirements>

---

## Summary

Phase 5 has three concerns: closing test coverage gaps (TEST-02), confirming the Dockerfile CMD is correct (DEPL-01), and deploying to Fly.io (DEPL-02).

**DEPL-01 is already complete.** The Dockerfile already has `CMD ["uv", "run", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]`. No code change is needed; the plan only needs to verify `docker build` succeeds.

**TEST-02 has two gaps.** The `detect_audio_settings` SocketIO event handler has zero tests. The `audio_stream` handler has a direct-call unit test (`test_audio_chunk_dropped_before_ws_ready`) but lacks a live SocketIO emit test that verifies bytes flow through to `ws.send_media()` when ws is ready. All other handlers (`toggle_transcription`, `start_file_streaming`, `stop_file_streaming`, `connect`, `disconnect`) are covered by existing tests. The coverage gaps are narrow and follow the same patterns already established in test_streaming.py.

**DEPL-02 requires flyctl CLI installation and `fly secrets set DEEPGRAM_API_KEY=...` before `fly deploy`.** The `FLY_API_TOKEN` is available in `/coding/deepgram-secretary/.env`. The fly.toml is fully configured with correct port (8080), single machine, HTTPS enforcement, and health checks. Post-deploy verification requires a SocketIO connection test to `deepgram-python-stt.fly.dev`.

**Primary recommendation:** Fix the two test coverage gaps first, verify `docker build` (no code changes needed for DEPL-01), then install flyctl and run `fly deploy` with the DEEPGRAM_API_KEY secret staged.

---

## Current Test State (Pre-Phase-5)

Running `uv run pytest tests/ -v` from the project root produces:

```
28 passed, 1 skipped, 5 warnings in 3.58s
```

The 1 skipped is `test_deepgram_sdk.py::test_deepgram_live_transcription` — intentionally skipped (legacy SDK v5 test, marked `pytest.mark.skip`). This is correct and should remain skipped.

### Handler Coverage Audit

| Handler | SocketIO Event | Test Coverage | Gap? |
|---------|---------------|---------------|------|
| `on_toggle_transcription` | `toggle_transcription` | test_app.py (2 tests) + test_streaming.py (2 tests) | None |
| `on_audio_stream` | `audio_stream` | test_streaming.py direct call (ws=None path only) | Partial — no live emit test with ws ready |
| `on_detect_audio_settings` | `detect_audio_settings` | None | YES — 0 tests |
| `on_start_file_streaming` | `start_file_streaming` | test_app.py (3 tests) | None |
| `on_stop_file_streaming` | `stop_file_streaming` | test_app.py (1 test) | None |
| `connect` | built-in connect | test_app.py (sio_client fixture) | None |
| `disconnect` | built-in disconnect | test_streaming.py (direct call) | None |
| `transcribe` (HTTP) | POST /transcribe | test_app.py (2 tests) | None |
| `upload` (HTTP) | POST /upload | test_app.py (1 test) | None |
| `index` (HTTP) | GET / | test_app.py (1 test) | None |

**Two gaps to close:** `detect_audio_settings` (no tests) and `audio_stream` ws-ready path (currently only ws=None path is tested).

---

## Standard Stack

### Core (already installed)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pytest | >=8.0 | Test runner | Already in dev-dependencies |
| pytest-asyncio | >=0.23,<1.0 | Async test support | Already configured with asyncio_mode=auto |
| aiohttp | >=3.9.0 | socketio.AsyncClient transport | Already in dev-dependencies |
| uvicorn | >=0.30.0 | ASGI server for test + prod | Already in dependencies |
| python-socketio | >=5.11.0 | AsyncClient for SocketIO tests | Already in dependencies |

### New tooling needed for deployment

| Tool | Installation | Purpose |
|------|-------------|---------|
| flyctl | `curl -L https://fly.io/install.sh \| sh` | Deploy to Fly.io |

### No new Python dependencies required

All testing patterns needed already exist in the project. No additional pip/uv installs needed for the test coverage phase.

---

## Architecture Patterns

### Pattern 1: Direct-Call Unit Test (for handlers with no live server needed)

Use this for `detect_audio_settings` — call `app.on_detect_audio_settings(sid)` directly, mock the `sio.emit` call.

```python
# Source: test_streaming.py existing pattern (test_audio_chunk_dropped_before_ws_ready)
async def test_detect_audio_settings_emits_audio_settings(sio_client):
    """detect_audio_settings must emit audio_settings event with sample_rate and channels."""
    future = asyncio.get_running_loop().create_future()

    @sio_client.on("audio_settings")
    def on_audio_settings(data):
        if not future.done():
            future.set_result(data)

    await sio_client.emit("detect_audio_settings")
    result = await asyncio.wait_for(future, timeout=5.0)
    assert "sample_rate" in result
    assert "channels" in result
    assert isinstance(result["sample_rate"], int)
    assert isinstance(result["channels"], int)
```

**Why this pattern works:** `detect_audio_settings` either calls `detect_audio_settings()` from common/audio_settings or falls back to defaults (16000/1) on exception. In the test container (no physical audio hardware), it will always fall into the except branch and emit defaults. The test accepts both paths.

### Pattern 2: Live SocketIO Emit Test (audio_stream with ws ready)

```python
# Source: test_streaming.py existing pattern (test_audio_chunk_dropped_before_ws_ready)
async def test_audio_stream_drops_silently_when_no_session(sio_client):
    """audio_stream with no active session must not crash the server."""
    # If audio arrives before toggle_transcription(start), ws is None — drops silently.
    # There is no response event for audio_stream; test confirms server stays alive.
    await sio_client.emit("audio_stream", b"\x00\x01\x02\x03")
    # Verify server is still responsive after receiving audio
    assert sio_client.connected
```

**Why this matters:** The `audio_stream` handler is on the hot path — it runs for every audio chunk during transcription. Testing that it drops silently (ws=None path) and doesn't crash the server is the key invariant. The ws-ready path (actually calling `ws.send_media()`) is only testable with a real Deepgram connection (requires a real API key), so it stays as a direct-call unit test.

### Pattern 3: Dockerfile Verification

```bash
# Verify docker build succeeds (no code changes expected)
docker build /coding/deepgram-python-stt -t deepgram-stt-test
# Verify CMD targets uvicorn
grep "CMD" /coding/deepgram-python-stt/Dockerfile
# Expected: CMD ["uv", "run", "uvicorn", "app:app", ...]
```

### Pattern 4: Fly.io Deployment

```bash
# Install flyctl (Linux)
curl -L https://fly.io/install.sh | sh

# Add to PATH
export FLYCTL_INSTALL="/root/.fly"
export PATH="$FLYCTL_INSTALL/bin:$PATH"

# Authenticate with existing token
export FLY_API_TOKEN="<from /coding/deepgram-secretary/.env>"

# Set the Deepgram API key as a fly secret (stage it, then deploy)
fly secrets set DEEPGRAM_API_KEY="<real-key>" --stage --app deepgram-python-stt

# Deploy
fly deploy --app deepgram-python-stt

# Verify health
fly status --app deepgram-python-stt
```

### Anti-Patterns to Avoid

- **Testing audio_stream ws-ready path without a real API key:** `ws.send_media()` requires an active Deepgram WebSocket. Mocking it at the unit test level is possible but the direct-call unit test already covers the ws=None path. The ws-ready path is implicitly tested by the live transcription flow (which Phase 3 verified manually). Don't add a brittle mock just to hit the branch.
- **Re-testing handlers already covered:** `toggle_transcription`, `start_file_streaming`, `stop_file_streaming` already have thorough tests. Don't duplicate them.
- **Using `fly secrets set` without `--stage` on a live app:** Without `--stage`, secrets set immediately triggers a redeploy of running machines. Use `--stage` then deploy in one controlled operation.
- **Deploying without DEEPGRAM_API_KEY secret:** The app falls back to `os.getenv("DEEPGRAM_API_KEY", "")` — empty string — which causes Deepgram 401 on all requests. The secret MUST be set before meaningful use.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Audio hardware detection in tests | Custom mock for sounddevice | Let the except fallback fire naturally | `detect_audio_settings()` already has a try/except that falls back to 16000/1 defaults; test containers have no audio hardware so the fallback path is guaranteed |
| Custom HTTP client for deployment verification | curl/httpx | `fly status` + health check endpoint | Fly.io provides `/` health check already configured in fly.toml |
| Re-implementing SocketIO test client | Custom WebSocket client | Existing `sio_client` fixture from conftest.py | Already established in Phase 2 — reuse session-scoped fixture |

---

## Common Pitfalls

### Pitfall 1: detect_audio_settings in test container always hits except branch
**What goes wrong:** Developer writes test expecting actual hardware values, test fails because container has no audio devices.
**Why it happens:** `sounddevice.query_devices()` raises on systems without audio hardware.
**How to avoid:** Write the test to accept any valid integer for `sample_rate` and `channels` (not specific values like 44100 or 2). The test verifies the event is emitted with correct shape, not specific values.
**Warning signs:** Test passes locally but fails in CI/container.

### Pitfall 2: Dockerfile CMD already correct — DEPL-01 is a verification task, not a code task
**What goes wrong:** Planner creates a task to "update" the Dockerfile CMD when it's already correct, causing wasted effort or accidental regression.
**Why it happens:** The requirement says "Dockerfile CMD updated from gunicorn to uvicorn" which implies it needs changing.
**How to avoid:** Read the Dockerfile first. Line 41 already has `CMD ["uv", "run", "uvicorn", "app:app", ...]`. The task is: verify, not change.
**Warning signs:** Any diff to the Dockerfile CMD line is wrong.

### Pitfall 3: fly deploy without API token set in environment
**What goes wrong:** `fly deploy` fails with authentication error.
**Why it happens:** flyctl needs either `fly auth login` (interactive) or `FLY_API_TOKEN` env var.
**How to avoid:** Export `FLY_API_TOKEN` from `/coding/deepgram-secretary/.env` before running fly commands.
**Warning signs:** `Error: not authenticated` from flyctl.

### Pitfall 4: Duplicate toggle_transcription lifecycle event listeners accumulate across tests
**What goes wrong:** Later tests in the session receive events from earlier tests because `sio_client.on(...)` adds listeners but doesn't remove them.
**Why it happens:** The session-scoped `sio_client` shares listener state across tests. `on()` accumulates handlers.
**How to avoid:** Use `asyncio.Future` with `if not future.done()` guard (already the pattern in test_app.py). For new tests, always check `future.done()` before setting result.
**Warning signs:** Flaky tests, unexpected `TimeoutError` on events that should fire quickly.

### Pitfall 5: Pending asyncio task warning at teardown
**What goes wrong:** `ERROR:asyncio:Task was destroyed but it is pending! task: <Task pending name='Task-15' coro=<AsyncSocket._send_ping()...>` appears after the test session.
**Why it happens:** python-socketio's engine.io async socket has a background ping task that gets orphaned when the test session ends. This is a known cosmetic warning from the library teardown.
**How to avoid:** Do not try to suppress it — it's benign and does not indicate test failure. Already occurring in current test run. Leave it.
**Warning signs:** This is NOT a warning sign of broken code; it is a known library quirk.

---

## Code Examples

### New test: detect_audio_settings (add to test_app.py or new test_audio.py)

```python
# Source: existing pattern from test_app.py (test_start_file_streaming_no_filename_emits_error)
async def test_detect_audio_settings_emits_audio_settings(sio_client):
    """detect_audio_settings must emit audio_settings with sample_rate and channels.
    In test container (no audio hardware), the except branch fires and returns defaults (16000/1).
    Test accepts any valid int values — does NOT assert specific hardware values.
    """
    future = asyncio.get_running_loop().create_future()

    @sio_client.on("audio_settings")
    def on_audio_settings(data):
        if not future.done():
            future.set_result(data)

    await sio_client.emit("detect_audio_settings")
    result = await asyncio.wait_for(future, timeout=5.0)
    assert "sample_rate" in result
    assert "channels" in result
    assert isinstance(result["sample_rate"], int)
    assert isinstance(result["channels"], int)
    assert result["sample_rate"] > 0
    assert result["channels"] > 0
```

### New test: audio_stream with no active session (add to test_app.py)

```python
# Source: existing pattern from test_streaming.py (test_audio_chunk_dropped_before_ws_ready)
async def test_audio_stream_when_not_streaming_does_not_crash(sio_client):
    """audio_stream bytes received with no active session drop silently.
    No response event is expected — test verifies the server stays alive and connected.
    """
    await sio_client.emit("audio_stream", b"\x00\x01\x02\x03\xff\xfe")
    # Give server a tick to process the event
    await asyncio.sleep(0.1)
    # Server must still be connected — audio_stream must not crash the handler
    assert sio_client.connected
```

### Deployment sequence

```bash
# Step 1: Install flyctl
curl -L https://fly.io/install.sh | sh
export PATH="$HOME/.fly/bin:$PATH"

# Step 2: Authenticate via token (non-interactive)
export FLY_API_TOKEN="<value from /coding/deepgram-secretary/.env>"

# Step 3: Verify docker build (DEPL-01 verification)
cd /coding/deepgram-python-stt
docker build . -t deepgram-stt-verify
# Expected: Successfully built <image_id>

# Step 4: Stage DEEPGRAM_API_KEY secret then deploy
fly secrets set DEEPGRAM_API_KEY="<real key from .env or project secrets>" \
    --stage --app deepgram-python-stt
fly deploy --app deepgram-python-stt

# Step 5: Verify deployment
fly status --app deepgram-python-stt
# Check health endpoint
curl https://deepgram-python-stt.fly.dev/
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| gunicorn CMD in Dockerfile | uvicorn CMD (already done) | Phase 1 decision | No action needed in Phase 5 |
| Flask-SocketIO test_client | UvicornTestServer + socketio.AsyncClient | Phase 2 | All tests use this pattern |
| websocket-client streaming | AsyncDeepgramClient.listen.v1.connect() | Phase 3 | SDK handles WebSocket lifecycle |

**Deprecated/outdated:**
- `test_deepgram_live_transcription` in test_deepgram_sdk.py: Legacy SDK v5 test, correctly marked as `pytest.mark.skip`. Leave it skipped.

---

## Open Questions

1. **Does the DEEPGRAM_API_KEY secret already exist on Fly.io from a previous deployment?**
   - What we know: The app is at deepgram-python-stt.fly.dev (the domain exists based on fly.toml)
   - What's unclear: Whether the secret was previously set or if this is a first deploy
   - Recommendation: Run `fly secrets list --app deepgram-python-stt` first. If the key exists, skip `fly secrets set`. If not, set it before deploying.

2. **Is flyctl installable in the current container environment?**
   - What we know: `fly` is not in PATH, `FLY_API_TOKEN` is available in `/coding/deepgram-secretary/.env`
   - What's unclear: Whether the container has internet access to run the flyctl install script
   - Recommendation: Try `curl -L https://fly.io/install.sh | sh` first. If network is restricted, the planner should include a fallback: download flyctl binary directly.

3. **audio_stream ws-ready path: is a SocketIO-level integration test worth adding?**
   - What we know: The ws-ready path calls `ws.send_media(audio)` — only exercisable with a real Deepgram WS connection
   - What's unclear: Whether TEST-02 requires this branch to be covered at the integration level
   - Recommendation: The direct-call unit test in test_streaming.py tests ws=None (the guard path). The ws-ready path is implicitly covered by Phase 3 manual verification of live transcription. For TEST-02, the session-scoped sio_client fixture would need to be in an actively-streaming state to test this. Skip the ws-ready path SocketIO test — it requires a real API key and is not reliably testable in CI. Document the coverage gap honestly.

---

## Sources

### Primary (HIGH confidence)
- Direct inspection of `/coding/deepgram-python-stt/Dockerfile` - CMD already uses uvicorn
- Direct inspection of `/coding/deepgram-python-stt/tests/` - all 4 test files read
- Direct inspection of `/coding/deepgram-python-stt/app.py` - all event handlers enumerated
- Direct inspection of `/coding/deepgram-python-stt/pyproject.toml` - dependencies and pytest config
- `uv run pytest tests/ -v` execution — 28 passed, 1 skipped confirmed
- `/coding/deepgram-python-stt/fly.toml` — deployment config verified

### Secondary (MEDIUM confidence)
- https://fly.io/docs/launch/deploy/ — `fly deploy` command documentation
- https://fly.io/docs/flyctl/install/ — flyctl Linux install: `curl -L https://fly.io/install.sh | sh`
- https://fly.io/docs/apps/secrets/ — `fly secrets set --stage` pattern

### Tertiary (LOW confidence)
- None

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries already installed, test suite running, versions confirmed from pyproject.toml
- Architecture: HIGH — test patterns directly visible in existing test files; Dockerfile CMD already correct
- Pitfalls: HIGH — discovered from direct test execution (pending task warning, coverage gaps confirmed by test collection)
- Deployment: MEDIUM — fly.toml and FLY_API_TOKEN confirmed; flyctl install command from official docs

**Research date:** 2026-03-05
**Valid until:** 2026-04-05 (stable libraries; fly.io deployment steps are stable)
