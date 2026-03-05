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


async def test_transcribe_returns_501():
    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        resp = await client.post("/transcribe", json={})
    assert resp.status_code == 501


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
