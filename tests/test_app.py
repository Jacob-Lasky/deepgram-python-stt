# tests/test_app.py
import asyncio
import os
from io import BytesIO

os.environ.setdefault("DEEPGRAM_API_KEY", "test-key")

from httpx import AsyncClient, ASGITransport
from app import fastapi_app  # HTTP sub-app only — not the socketio.ASGIApp wrapper


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


async def test_transcribe_no_source_returns_400():
    """No url or filename: route returns 400 immediately without calling Deepgram."""
    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.post("/transcribe", json={})
    assert resp.status_code == 400
    assert "error" in resp.json()


async def test_transcribe_url_source_returns_non_501():
    """With test-key, Deepgram returns 401. Route propagates as error JSON.
    Key assertion: route is no longer a 501 stub — it actually calls Deepgram."""
    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/transcribe",
            json={"url": "https://static.deepgram.com/examples/Bueller-Life-moves-pretty-fast.wav"},
        )
    # With test-key: 401 from Deepgram propagated as error JSON
    # With real key: 200 with transcription
    assert resp.status_code != 501
    assert "error" in resp.json() or "results" in resp.json()


# ---- SocketIO events (uses session-scoped sio_client from conftest) ----

async def test_socketio_connects(sio_client):
    assert sio_client.connected


async def test_toggle_transcription_start_emits_lifecycle_event(sio_client):
    """With test-key, Deepgram auth fails (401) so stream_finished is emitted.
    Key assertion: a lifecycle event IS emitted — the handler does not hang.
    Accepts stream_started (valid key) or stream_finished (invalid key / auth error).
    """
    future = asyncio.get_running_loop().create_future()

    @sio_client.on("stream_started")
    def on_ss(data):
        if not future.done():
            future.set_result(("stream_started", data))

    @sio_client.on("stream_finished")
    def on_sf(data):
        if not future.done():
            future.set_result(("stream_finished", data))

    await sio_client.emit("toggle_transcription", {"action": "start", "params": {}})
    event_name, result = await asyncio.wait_for(future, timeout=15.0)
    assert event_name in ("stream_started", "stream_finished")
    assert "request_id" in result


async def test_toggle_transcription_stop_emits_stream_finished(sio_client):
    """Emitting stop when not streaming must emit stream_finished immediately."""
    future = asyncio.get_running_loop().create_future()

    @sio_client.on("stream_finished")
    def on_stream_finished(data):
        if not future.done():
            future.set_result(data)

    await sio_client.emit("toggle_transcription", {"action": "stop", "params": {}})
    result = await asyncio.wait_for(future, timeout=10.0)
    assert "request_id" in result


# ---- File streaming SocketIO events ----

async def test_start_file_streaming_no_filename_emits_error(sio_client):
    """Emitting start_file_streaming without a filename must emit stream_error."""
    future = asyncio.get_running_loop().create_future()

    @sio_client.on("stream_error")
    def on_error(data):
        if not future.done():
            future.set_result(data)

    await sio_client.emit("start_file_streaming", {})
    result = await asyncio.wait_for(future, timeout=5.0)
    assert "message" in result


async def test_start_file_streaming_with_filename_emits_lifecycle_event(sio_client):
    """Upload a tiny fake WAV, emit start_file_streaming, expect stream_started or stream_finished.
    With test-key, Deepgram auth fails so stream_finished is acceptable.
    """
    # Upload the file first using a separate HTTP client
    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        await client.post(
            "/upload",
            files={"file": ("test_file.wav", BytesIO(b"fake audio"), "audio/wav")},
        )

    future = asyncio.get_running_loop().create_future()

    @sio_client.on("stream_started")
    def on_ss(data):
        if not future.done():
            future.set_result(("stream_started", data))

    @sio_client.on("stream_finished")
    def on_sf(data):
        if not future.done():
            future.set_result(("stream_finished", data))

    await sio_client.emit("start_file_streaming", {"filename": "test_file.wav", "params": {}})
    event_name, result = await asyncio.wait_for(future, timeout=15.0)
    assert event_name in ("stream_started", "stream_finished")
    assert "request_id" in result


async def test_stop_file_streaming_when_not_streaming_emits_stream_finished(sio_client):
    """Emitting stop_file_streaming when not streaming must emit stream_finished immediately."""
    future = asyncio.get_running_loop().create_future()

    @sio_client.on("stream_finished")
    def on_sf(data):
        if not future.done():
            future.set_result(data)

    await sio_client.emit("stop_file_streaming", {})
    result = await asyncio.wait_for(future, timeout=5.0)
    assert "request_id" in result


# ---- Audio device / raw audio events ----

async def test_detect_audio_settings_emits_audio_settings(sio_client):
    """Emitting detect_audio_settings must receive an audio_settings response
    with positive integer sample_rate and channels values.
    In CI (no audio hardware) the handler falls back to defaults (16000, 1).
    Test accepts any valid positive int — not hardcoded values.
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


async def test_audio_stream_when_not_streaming_does_not_crash(sio_client):
    """Emitting audio_stream bytes when no session is active (ws=None) must drop
    silently without crashing the server. Verifies the guard path in on_audio_stream.
    """
    await sio_client.emit("audio_stream", b"\x00\x01\x02\x03\xff\xfe")
    await asyncio.sleep(0.1)
    assert sio_client.connected


# --- /api/param-gating ----------------------------------------------------
#
# The browser used to keep its own copies of the mode lists and they had
# drifted, so the URL bar advertised a request that was not the one being sent.
# These tests are what stops that happening again: if options.py grows a rule
# the endpoint does not carry, the frontend cannot see it.


def test_param_gating_matches_options_module():
    import asyncio

    import app as app_mod
    from stt.options import (
        BATCH_ONLY, DENIED_PARAMS, FLUX_MULTILINGUAL_MODEL, FLUX_MULTILINGUAL_ONLY,
        FLUX_PARAMS, FLUX_RANGES, FLUX_REDACT_VALUES, INTERNAL_PARAMS,
        PARAM_ALIASES, REPEATABLE_PARAMS, STREAMING_ONLY, ZERO_MEANS_UNSET,
    )

    body = asyncio.run(app_mod.param_gating())
    assert body["streaming_only"] == sorted(STREAMING_ONLY)
    assert body["batch_only"] == sorted(BATCH_ONLY)
    assert body["internal"] == sorted(INTERNAL_PARAMS)
    assert body["denied"] == sorted(DENIED_PARAMS)
    assert body["aliases"] == PARAM_ALIASES
    assert body["repeatable"] == sorted(REPEATABLE_PARAMS)
    assert body["zero_means_unset"] == sorted(ZERO_MEANS_UNSET)
    assert body["flux"]["params"] == sorted(FLUX_PARAMS)
    assert body["flux"]["multilingual_only"] == sorted(FLUX_MULTILINGUAL_ONLY)
    assert body["flux"]["multilingual_model"] == FLUX_MULTILINGUAL_MODEL
    assert body["flux"]["redact_values"] == sorted(FLUX_REDACT_VALUES)
    assert body["flux"]["ranges"] == {k: list(v) for k, v in FLUX_RANGES.items()}


def test_param_gating_is_open_to_anonymous_callers():
    """The params panel has to render for the visitors this app demos to, and
    the endpoint describes a public API surface rather than spending anything.
    Asserting on the route's dependencies rather than on a response, because the
    thing that would break this is someone adding _enforce_access to it."""
    import app as app_mod

    routes = [r for r in app_mod.fastapi_app.routes
              if getattr(r, "path", None) == "/api/param-gating"]
    assert routes, "/api/param-gating is not registered"
    assert routes[0].dependencies == []


def test_param_gating_is_json_serialisable_without_sets():
    """Sets are not JSON. Returning one raises at response time, not import
    time, so it would ship green and 500 in the browser."""
    import asyncio
    import json as json_mod

    import app as app_mod

    json_mod.dumps(asyncio.run(app_mod.param_gating()))


def test_unfinished_turn_notice_names_the_cause_and_keeps_the_text():
    import app as app_mod

    notice = app_mod._unfinished_turn_notice("Actually, wait. Can you tell me a")
    assert "eot_timeout_ms" in notice["message"]
    assert "trailing silence" in notice["message"]
    assert notice["unfinalized_transcript"] == "Actually, wait. Can you tell me a"
    # The toast auto-dismisses, so it gets the short form.
    assert len(notice["summary"]) < len(notice["message"])


def test_pending_turn_tracks_only_unfinalised_flux_turns():
    """The notice must fire when a stream stops mid-turn and stay quiet
    otherwise, so what sets and clears pending_turn is the whole contract."""
    import asyncio

    import app as app_mod

    sid = "test-pending"
    app_mod._sessions[sid] = {}
    handler = app_mod._stream_message_handler(sid)

    def feed(event, transcript):
        asyncio.run(handler({
            "type": "TurnInfo", "event": event, "transcript": transcript,
            "turn_index": 0,
        }))

    try:
        feed("StartOfTurn", "Actually,")
        assert app_mod._sessions[sid]["pending_turn"] == "Actually,"

        feed("Update", "Actually, wait")
        assert app_mod._sessions[sid]["pending_turn"] == "Actually, wait"

        # EndOfTurn settles the turn: nothing is owed to the user any more.
        feed("EndOfTurn", "Actually, wait.")
        assert app_mod._sessions[sid]["pending_turn"] is None

        # An empty Update between turns must not resurrect the notice.
        feed("Update", "")
        assert app_mod._sessions[sid]["pending_turn"] is None
    finally:
        app_mod._sessions.pop(sid, None)


def test_v1_results_never_set_a_pending_turn():
    """v1 has no turns, so a v1 stream must never raise the Flux notice."""
    import asyncio

    import app as app_mod
    from deepgram.listen.v1.types import ListenV1Results

    sid = "test-pending-v1"
    app_mod._sessions[sid] = {}
    handler = app_mod._stream_message_handler(sid)
    # A real model, not a mock: the handler branches on isinstance, so a mock
    # that is not one would pass this test by taking the wrong branch.
    # model_construct skips validation — a valid Results needs a whole metadata
    # tree this test does not care about, and inventing one would obscure what
    # is being asserted.
    # The SDK's model_construct builds nested fields from plain dicts.
    msg = ListenV1Results.model_construct(
        type="Results", start=0.0, is_final=False,
        channel={"alternatives": [{"transcript": "hello", "confidence": 1.0, "words": []}]},
    )
    assert msg.channel.alternatives[0].transcript == "hello"
    try:
        asyncio.run(handler(msg))
        assert "pending_turn" not in app_mod._sessions[sid]
    finally:
        app_mod._sessions.pop(sid, None)
