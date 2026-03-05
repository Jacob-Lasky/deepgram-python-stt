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


async def test_toggle_transcription_start_emits_stream_started(sio_client):
    future = asyncio.get_running_loop().create_future()

    @sio_client.on("stream_started")
    def on_stream_started(data):
        if not future.done():  # guard: session-scoped handler may fire again in later tests
            future.set_result(data)

    await sio_client.emit("toggle_transcription", {"action": "start", "params": {}})
    result = await asyncio.wait_for(future, timeout=5.0)
    assert "request_id" in result


async def test_toggle_transcription_stop_emits_stream_finished(sio_client):
    future = asyncio.get_running_loop().create_future()

    @sio_client.on("stream_finished")
    def on_stream_finished(data):
        if not future.done():
            future.set_result(data)

    await sio_client.emit("toggle_transcription", {"action": "stop", "params": {}})
    result = await asyncio.wait_for(future, timeout=5.0)
    assert "request_id" in result
