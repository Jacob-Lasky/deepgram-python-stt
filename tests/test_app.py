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
