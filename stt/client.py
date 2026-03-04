import json
import logging
import mimetypes
import threading
import urllib.parse

import requests
import websocket  # websocket-client (NOT the websockets package)

from .options import Mode, clean_params

logger = logging.getLogger(__name__)


class STTClient:
    def __init__(self, api_key: str, base_url: str = "api.deepgram.com"):
        self.api_key = api_key
        self.base_url = base_url
        self._ws = None       # active WebSocket (from create_connection)
        self._stream_thread = None

    def build_url(self, params: dict, mode: Mode) -> str:
        """Return the full Deepgram URL that would be used for these params."""
        clean = clean_params(params, mode)
        protocol = "wss" if mode == Mode.STREAMING else "https"
        base = params.get("base_url", self.base_url)

        parts = []
        for k, v in clean.items():
            if isinstance(v, list):
                for item in v:
                    val = str(item).lower() if isinstance(item, bool) else str(item)
                    parts.append(f"{k}={urllib.parse.quote(val)}")
            else:
                val = str(v).lower() if isinstance(v, bool) else str(v)
                parts.append(f"{k}={urllib.parse.quote(val)}")

        qs = "&".join(parts)
        return f"{protocol}://{base}/v1/listen?{qs}" if qs else f"{protocol}://{base}/v1/listen"

    def open_stream(self, params: dict, on_transcript, on_error=None, on_close=None):
        """
        Open a streaming WebSocket to Deepgram using websocket.create_connection().
        Simpler than WebSocketApp — direct socket, fully gevent-compatible.
        Returns self so callers can use send_media() / send_close_stream().
        """
        url = self.build_url(params, Mode.STREAMING)
        logger.info("Opening Deepgram stream: %s", url)

        try:
            self._ws = websocket.create_connection(
                url,
                header={"Authorization": f"Token {self.api_key}"},
            )
        except Exception as e:
            raise RuntimeError(f"Deepgram connection failed: {e}")

        logger.info("Deepgram WebSocket opened")

        def recv_loop():
            try:
                while True:
                    msg = self._ws.recv()
                    if msg is None:
                        break
                    if isinstance(msg, bytes):
                        continue
                    try:
                        data = json.loads(msg)
                    except Exception:
                        continue
                    msg_type = data.get("type")
                    if msg_type == "Results":
                        try:
                            alt = data["channel"]["alternatives"][0]
                            transcript = alt.get("transcript", "")
                            is_final = bool(transcript) and bool(data.get("is_final", False))
                            on_transcript(data, is_final)
                        except (KeyError, IndexError) as e:
                            logger.warning("Error parsing transcript: %s", e)
                    elif msg_type == "Metadata":
                        logger.debug("Deepgram metadata: %s", data)
                    else:
                        logger.debug("Deepgram message type=%s", msg_type)
            except Exception as e:
                logger.error("Deepgram recv_loop error: %s", e)
                if on_error:
                    on_error(str(e))
            finally:
                logger.info("Deepgram WebSocket closed")
                self._ws = None
                if on_close:
                    on_close()

        self._stream_thread = threading.Thread(target=recv_loop, daemon=True)
        self._stream_thread.start()

        return self

    def send_media(self, data: bytes):
        """Send a binary audio chunk to the active Deepgram stream."""
        if self._ws:
            try:
                self._ws.send_binary(data)
            except Exception as e:
                logger.error("send_media() failed: %s", e)

    def send_close_stream(self):
        """Send the Deepgram CloseStream control message."""
        if self._ws:
            try:
                self._ws.send(json.dumps({"type": "CloseStream"}))
            except Exception as e:
                logger.warning("send_close_stream() failed: %s", e)

    def close_stream(self):
        """Close the active Deepgram stream."""
        ws = self._ws
        self._ws = None
        if ws:
            try:
                ws.send(json.dumps({"type": "CloseStream"}))
            except Exception:
                pass
            try:
                ws.close()
            except Exception:
                pass

    def transcribe_batch(self, audio_source, params: dict) -> dict:
        """
        Transcribe audio using the pre-recorded (batch) API.
        audio_source: file path (str), URL (str starting with http), or bytes
        Returns full Deepgram response dict.
        """
        clean = clean_params(params, Mode.BATCH)
        base = params.get("base_url", self.base_url)
        url = f"https://{base}/v1/listen"

        headers = {
            "Authorization": f"Token {self.api_key}",
            "Accept": "application/json",
        }

        if isinstance(audio_source, str) and audio_source.startswith("http"):
            headers["Content-Type"] = "application/json"
            response = requests.post(url, headers=headers, json={"url": audio_source}, params=clean, timeout=600)
        elif isinstance(audio_source, bytes):
            headers["Content-Type"] = "audio/wav"
            response = requests.post(url, headers=headers, data=audio_source, params=clean, timeout=600)
        else:
            content_type, _ = mimetypes.guess_type(str(audio_source))
            headers["Content-Type"] = content_type or "audio/wav"
            with open(audio_source, "rb") as f:
                response = requests.post(url, headers=headers, data=f, params=clean, timeout=600)

        response.raise_for_status()
        return response.json()
