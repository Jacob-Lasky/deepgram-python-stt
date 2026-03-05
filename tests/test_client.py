import json
import threading
from unittest.mock import MagicMock, patch, call
import pytest
from stt.client import STTClient
from stt.options import Mode


API_KEY = "test-key"


# ---- build_url ----

def test_build_url_streaming_scheme():
    client = STTClient(API_KEY)
    url = client.build_url({"model": "nova-3"}, Mode.STREAMING)
    assert url.startswith("wss://")


def test_build_url_batch_scheme():
    client = STTClient(API_KEY)
    url = client.build_url({"model": "nova-3"}, Mode.BATCH)
    assert url.startswith("https://")


def test_build_url_contains_params():
    client = STTClient(API_KEY)
    url = client.build_url({"model": "nova-3", "language": "en"}, Mode.STREAMING)
    assert "model=nova-3" in url
    assert "language=en" in url


def test_build_url_booleans_lowercase():
    client = STTClient(API_KEY)
    url = client.build_url({"smart_format": True, "diarize": False}, Mode.STREAMING)
    assert "smart_format=true" in url
    assert "diarize" not in url  # False is filtered by clean_params


def test_build_url_custom_base_url():
    client = STTClient(API_KEY)
    url = client.build_url({"model": "nova-3", "base_url": "custom.deepgram.com"}, Mode.STREAMING)
    assert "custom.deepgram.com" in url


def test_build_url_repeated_list_params():
    client = STTClient(API_KEY)
    url = client.build_url({"redact": ["pci", "ssn"]}, Mode.STREAMING)
    assert url.count("redact=") == 2
    assert "pci" in url
    assert "ssn" in url


def test_build_url_strips_batch_only_from_streaming():
    client = STTClient(API_KEY)
    url = client.build_url({"model": "nova-3", "paragraphs": True}, Mode.STREAMING)
    assert "paragraphs" not in url


# ---- transcribe_batch ----

@patch("stt.client.requests.post")
def test_transcribe_batch_url_source(mock_post):
    mock_post.return_value = MagicMock(status_code=200, json=lambda: {"results": {}})
    mock_post.return_value.raise_for_status = MagicMock()

    client = STTClient(API_KEY)
    client.transcribe_batch("https://example.com/audio.wav", {"model": "nova-3"})

    args, kwargs = mock_post.call_args
    assert kwargs["json"] == {"url": "https://example.com/audio.wav"}
    assert kwargs["headers"]["Authorization"] == f"Token {API_KEY}"


@patch("stt.client.requests.post")
def test_transcribe_batch_bytes_source(mock_post):
    mock_post.return_value = MagicMock(status_code=200, json=lambda: {"results": {}})
    mock_post.return_value.raise_for_status = MagicMock()

    client = STTClient(API_KEY)
    client.transcribe_batch(b"\x00\x01\x02", {"model": "nova-3"})

    args, kwargs = mock_post.call_args
    assert kwargs["data"] == b"\x00\x01\x02"
    assert kwargs["headers"]["Content-Type"] == "audio/wav"


@patch("stt.client.requests.post")
def test_transcribe_batch_returns_json(mock_post):
    expected = {"results": {"channels": [{"alternatives": [{"transcript": "hello world"}]}]}}
    mock_post.return_value = MagicMock(status_code=200, json=lambda: expected)
    mock_post.return_value.raise_for_status = MagicMock()

    client = STTClient(API_KEY)
    result = client.transcribe_batch("https://example.com/audio.wav", {})
    assert result == expected


# ---- open_stream ----

def _make_mock_ws(messages):
    """Return a mock WebSocket that yields messages then raises ConnectionError to end recv_loop."""
    ws = MagicMock()
    recv_values = [json.dumps(m) for m in messages] + [Exception("closed")]

    def recv_side_effect():
        val = recv_values.pop(0)
        if isinstance(val, Exception):
            raise val
        return val

    ws.recv.side_effect = recv_side_effect
    return ws


@patch("stt.client.websocket.create_connection")
def test_open_stream_calls_on_transcript(mock_create):
    transcript_msg = {
        "type": "Results",
        "is_final": True,
        "channel": {"alternatives": [{"transcript": "hello world", "words": []}]},
    }
    mock_create.return_value = _make_mock_ws([transcript_msg])

    received = []
    done = threading.Event()

    def on_transcript(data, is_final):
        received.append((data, is_final))
        done.set()

    client = STTClient(API_KEY)
    client.open_stream({}, on_transcript)
    done.wait(timeout=2)

    assert len(received) == 1
    assert received[0][1] is True  # is_final


@patch("stt.client.websocket.create_connection")
def test_open_stream_interim_not_final(mock_create):
    interim_msg = {
        "type": "Results",
        "is_final": False,
        "channel": {"alternatives": [{"transcript": "hel", "words": []}]},
    }
    mock_create.return_value = _make_mock_ws([interim_msg])

    received = []
    done = threading.Event()

    def on_transcript(data, is_final):
        received.append(is_final)
        done.set()

    client = STTClient(API_KEY)
    client.open_stream({}, on_transcript)
    done.wait(timeout=2)

    # Empty interim transcript → is_final=False (even if is_final flag is False)
    assert received[0] is False


@patch("stt.client.websocket.create_connection")
def test_open_stream_calls_on_close(mock_create):
    mock_create.return_value = _make_mock_ws([])  # immediately raises, triggering close

    closed = threading.Event()
    client = STTClient(API_KEY)
    client.open_stream({}, lambda d, f: None, on_close=lambda: closed.set())
    closed.wait(timeout=2)
    assert closed.is_set()


@patch("stt.client.websocket.create_connection")
def test_send_media_calls_send_binary(mock_create):
    ws = MagicMock()
    ws.recv.side_effect = Exception("closed")
    mock_create.return_value = ws

    client = STTClient(API_KEY)
    client.open_stream({}, lambda d, f: None)
    client._ws = ws  # ensure ws is set before send

    client.send_media(b"\xff\xfe")
    ws.send_binary.assert_called_with(b"\xff\xfe")


@patch("stt.client.websocket.create_connection")
def test_close_stream_sends_close_message(mock_create):
    ws = MagicMock()
    ws.recv.side_effect = Exception("closed")
    mock_create.return_value = ws

    client = STTClient(API_KEY)
    client.open_stream({}, lambda d, f: None)
    client._ws = ws

    client.close_stream()
    sent = ws.send.call_args[0][0]
    assert json.loads(sent) == {"type": "CloseStream"}
