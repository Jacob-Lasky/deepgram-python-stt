# tests/test_streaming.py
# Tests for Deepgram SDK streaming implementation in app.py
# Uses pytest-asyncio auto mode (no @pytest.mark.asyncio needed)
import asyncio
import os
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

os.environ.setdefault("DEEPGRAM_API_KEY", "test-key")

import app
from deepgram.core.events import EventType


# ---------------------------------------------------------------------------
# Mock Deepgram SDK classes
# ---------------------------------------------------------------------------

class MockAsyncV1SocketClient:
    """Simulates deepgram-sdk AsyncV1SocketClient for unit tests."""

    def __init__(self, controlled_messages=None):
        self.controlled_messages = controlled_messages or []
        self.send_media_calls: list = []
        self.send_keep_alive_calls: int = 0
        self.send_close_stream_called: bool = False
        self._callbacks: dict = {}

    def on(self, event_type, callback):
        self._callbacks[event_type] = callback

    async def send_media(self, data):
        self.send_media_calls.append(data)

    async def send_keep_alive(self):
        self.send_keep_alive_calls += 1

    async def send_close_stream(self):
        self.send_close_stream_called = True

    async def start_listening(self):
        """Simulate Deepgram delivering messages then closing the stream."""
        for msg in self.controlled_messages:
            cb = self._callbacks.get(EventType.MESSAGE)
            if cb:
                await cb(msg)
        # Simulate stream end — returns normally


class MockConnectContextManager:
    """Async context manager that yields a MockAsyncV1SocketClient."""

    def __init__(self, mock_ws):
        self.mock_ws = mock_ws

    async def __aenter__(self):
        return self.mock_ws

    async def __aexit__(self, *args):
        pass


class MockListenV1:
    def __init__(self, mock_ws):
        self.mock_ws = mock_ws

    def connect(self, **kwargs):
        return MockConnectContextManager(self.mock_ws)


class MockListen:
    def __init__(self, mock_ws):
        self.v1 = MockListenV1(mock_ws)


class MockAsyncDeepgramClient:
    """Simulates AsyncDeepgramClient for unit tests."""

    def __init__(self, mock_ws=None):
        if mock_ws is None:
            mock_ws = MockAsyncV1SocketClient()
        self.mock_ws = mock_ws
        self.listen = MockListen(mock_ws)


# ---------------------------------------------------------------------------
# Static / structural tests (no server needed)
# ---------------------------------------------------------------------------

def test_no_websocket_client_import():
    """app.py must NOT import websocket-client; must use deepgram SDK instead."""
    app_text = Path("/coding/deepgram-python-stt/app.py").read_text()
    assert "import websocket" not in app_text, "websocket-client import found in app.py"
    assert "AsyncDeepgramClient" in app_text, "AsyncDeepgramClient not found in app.py"


def test_no_threading_in_app():
    """app.py must not import threading or use time.sleep in streaming path."""
    app_text = Path("/coding/deepgram-python-stt/app.py").read_text()
    assert "import threading" not in app_text, "import threading found in app.py"
    assert "threading.Thread" not in app_text, "threading.Thread found in app.py"
    assert "threading.Event" not in app_text, "threading.Event found in app.py"
    assert "time.sleep" not in app_text, "time.sleep found in app.py"


def test_websocket_client_not_in_pyproject():
    """websocket-client must not be a dependency."""
    pyproject = Path("/coding/deepgram-python-stt/pyproject.toml").read_text()
    assert "websocket-client" not in pyproject, "websocket-client found in pyproject.toml"


def test_sessions_dict_exists():
    """app._sessions must be a module-level dict."""
    assert hasattr(app, "_sessions"), "app._sessions not found"
    assert isinstance(app._sessions, dict), "app._sessions is not a dict"


def test_streaming_task_callable():
    """app.streaming_task must be a coroutine function."""
    assert hasattr(app, "streaming_task"), "app.streaming_task not found"
    assert asyncio.iscoroutinefunction(app.streaming_task), "streaming_task is not async"


# ---------------------------------------------------------------------------
# Direct-call unit tests (no live server needed)
# ---------------------------------------------------------------------------

async def test_audio_chunk_dropped_before_ws_ready():
    """on_audio_stream with ws=None must not raise — drops silently."""
    fake_sid = "test-sid-audio-drop"
    app._sessions[fake_sid] = {
        "stop_event": asyncio.Event(),
        "ws": None,
        "request_id": None,
        "task": None,
    }
    try:
        # Must not raise even when ws is None
        await app.on_audio_stream(fake_sid, b"\x00\x01\x02\x03")
    finally:
        app._sessions.pop(fake_sid, None)


async def test_disconnect_cleans_sessions():
    """disconnect must remove sid from _sessions."""
    fake_sid = "test-sid-disconnect"
    app._sessions[fake_sid] = {
        "stop_event": asyncio.Event(),
        "ws": None,
        "request_id": None,
        "task": None,
    }
    await app.disconnect(fake_sid)
    assert fake_sid not in app._sessions, "disconnect did not clean up _sessions"


# ---------------------------------------------------------------------------
# Integration tests using session-scoped sio_client (live server)
# ---------------------------------------------------------------------------

async def test_toggle_transcription_start_emits_lifecycle_event(sio_client):
    """With test-key, Deepgram auth fails → stream_finished emitted.
    Key assertion: a lifecycle event IS emitted — handler does not hang.
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
    def on_sf(data):
        if not future.done():
            future.set_result(data)

    await sio_client.emit("toggle_transcription", {"action": "stop", "params": {}})
    result = await asyncio.wait_for(future, timeout=10.0)
    assert "request_id" in result
