import json
import os
import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("DEEPGRAM_API_KEY", "test-key")

from app import app, socketio


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def socket_client():
    app.config["TESTING"] = True
    return socketio.test_client(app)


# ---- HTTP routes ----

def test_index_returns_html(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"<!DOCTYPE html>" in resp.data or b"<html" in resp.data


def test_upload_no_file_returns_400(client):
    resp = client.post("/upload", data={})
    assert resp.status_code == 400
    assert b"No file" in resp.data


def test_upload_file(client, tmp_path):
    from io import BytesIO
    resp = client.post(
        "/upload",
        data={"file": (BytesIO(b"fake audio data"), "test.wav")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    body = json.loads(resp.data)
    assert body["filename"] == "test.wav"
    assert body["size"] > 0


def test_transcribe_no_body_returns_400(client):
    resp = client.post("/transcribe", content_type="application/json", data="{}")
    assert resp.status_code == 400


def test_transcribe_no_source_returns_400(client):
    resp = client.post(
        "/transcribe",
        content_type="application/json",
        data=json.dumps({"params": {}}),
    )
    assert resp.status_code == 400
    body = json.loads(resp.data)
    assert "No audio source" in body["error"]


@patch("app.STTClient")
def test_transcribe_url_source(mock_cls, client):
    mock_instance = MagicMock()
    mock_instance.transcribe_batch.return_value = {"results": {}}
    mock_cls.return_value = mock_instance

    resp = client.post(
        "/transcribe",
        content_type="application/json",
        data=json.dumps({"url": "https://example.com/audio.wav", "params": {}}),
    )
    assert resp.status_code == 200
    mock_instance.transcribe_batch.assert_called_once()


# ---- SocketIO events ----

def test_socketio_connect(socket_client):
    assert socket_client.is_connected()


def test_socketio_disconnect(socket_client):
    socket_client.disconnect()
    assert not socket_client.is_connected()


@patch("app.STTClient")
def test_toggle_transcription_stop(mock_cls, socket_client):
    mock_instance = MagicMock()
    mock_cls.return_value = mock_instance

    socket_client.emit("toggle_transcription", {"action": "stop", "params": {}})
    # Should not raise; close_stream may or may not be called depending on session state


@patch("stt.client.websocket.create_connection")
def test_toggle_transcription_start_emits_stream_started(mock_create_conn, socket_client):
    mock_ws = MagicMock()
    mock_ws.recv.return_value = None  # stops recv_loop immediately
    mock_create_conn.return_value = mock_ws

    socket_client.emit("toggle_transcription", {"action": "start", "params": {"model": "nova-3"}})

    received = socket_client.get_received()
    event_names = [r["name"] for r in received]
    assert "stream_started" in event_names


def test_audio_stream_no_connection_does_not_raise(socket_client):
    # Emitting audio with no active connection should silently no-op
    socket_client.emit("audio_stream", b"\x00\x01\x02")
    # No assertion needed — just verifying no exception is raised


def test_detect_audio_settings_emits_response(socket_client):
    import sys
    mock_module = MagicMock()
    mock_module.detect_audio_settings.return_value = {"sample_rate": 16000, "max_input_channels": 1}
    with patch.dict(sys.modules, {"common.audio_settings": mock_module}):
        socket_client.emit("detect_audio_settings")
        received = socket_client.get_received()
        event_names = [r["name"] for r in received]
        assert "audio_settings" in event_names
