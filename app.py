import asyncio
import inspect
import json as json_mod
import logging
import os
import re
import secrets
import tempfile
from collections import defaultdict, deque
from time import monotonic
import urllib.parse
from pathlib import Path

from mutagen import File as MutagenFile

import httpx
import socketio
import websockets
from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType
from deepgram.listen.v1.types import ListenV1Results, ListenV1Metadata
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from stt.options import Mode, query_string, serialize_params

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PORT = int(os.getenv("PORT", 8001))
TEMP_DIR = Path(tempfile.gettempdir()) / "deepgram-stt"
TEMP_DIR.mkdir(exist_ok=True)

# --- Security: outbound destination allowlist ---
# DO NOT interpolate a caller-supplied base_url into an outbound URL that
# carries DEEPGRAM_API_KEY. A free-form base_url let any caller choose the
# destination host AND inject path/query, which forwarded this server's API
# key to a host of their choosing. Verified exploitable 2026-07-25: a request
# with base_url="postman-echo.com/post?x=" delivered "Token <key>" to that
# third party. The allowlist is operator-controlled via env, never caller
# controlled, and the host must be a BARE hostname with optional port so no
# path, query, fragment, or userinfo can be smuggled in.
DEEPGRAM_HOST = "api.deepgram.com"
ALLOWED_STT_HOSTS = {
    h.strip().lower()
    for h in os.getenv("ALLOWED_STT_HOSTS", DEEPGRAM_HOST).split(",")
    if h.strip()
}
_BARE_HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:\d{1,5})?$")

# --- Tiered access ---
# This app is BOTH a capability demo and a diagnostic tool, so a visitor with
# no token must get a working app, mic streaming included: a dead UI demos
# nothing. Access is therefore a budget, not a wall. Anonymous callers get a
# small allowance; a valid token lifts the limits for the operator and the
# sweep harnesses in scripts/.
#
# Per-IP limiting alone does NOT protect the bill, because IPs are cheap. The
# GLOBAL anonymous ceiling is the load-bearing control: per-IP stops one person
# hammering, the global cap is what makes a distributed attempt pointless.
# DO NOT remove the global ceiling on the grounds that per-IP covers it.
#
# In-process counters are correct here rather than Redis: fly.toml pins this app
# to a single machine with a single worker because python-socketio keeps session
# state in memory, so there is no second process to share state with. If that
# ever changes, these counters need an external store and so does the socket map.
ANON_ACCESS = os.getenv("ANON_ACCESS", "true").strip().lower() not in ("0", "false", "no", "off")
ANON_RATE_LIMIT = int(os.getenv("ANON_RATE_LIMIT", 15))
ANON_RATE_WINDOW_S = int(os.getenv("ANON_RATE_WINDOW_S", 300))
ANON_GLOBAL_LIMIT = int(os.getenv("ANON_GLOBAL_LIMIT", 300))
ANON_GLOBAL_WINDOW_S = int(os.getenv("ANON_GLOBAL_WINDOW_S", 3600))
ANON_MAX_CONCURRENT_STREAMS = int(os.getenv("ANON_MAX_CONCURRENT_STREAMS", 3))
ANON_MAX_STREAM_SECONDS = int(os.getenv("ANON_MAX_STREAM_SECONDS", 120))

# Per-request caps. TTS bills per character and STT per audio-minute, so these
# are spend caps, not validation niceties. The anonymous tier is tighter.
MAX_TTS_CHARS = int(os.getenv("MAX_TTS_CHARS", 2000))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", 100 * 1024 * 1024))
ANON_MAX_TTS_CHARS = int(os.getenv("ANON_MAX_TTS_CHARS", 400))
ANON_MAX_UPLOAD_BYTES = int(os.getenv("ANON_MAX_UPLOAD_BYTES", 10 * 1024 * 1024))

# Sliding-window hit logs. Keyed by client IP, plus one global log.
_anon_hits: dict[str, deque] = defaultdict(deque)
_anon_global_hits: deque = deque()


def _prune(log: deque, cutoff: float) -> None:
    while log and log[0] < cutoff:
        log.popleft()


def _client_ip(request: Request) -> str:
    """Best-effort client IP. Behind Fly's proxy the real one is in a header."""
    forwarded = request.headers.get("fly-client-ip") or request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _anon_rate_limit(ip: str) -> str | None:
    """Charge one anonymous request against both windows.

    Returns None when allowed, or a human-readable reason when over a limit.
    Nothing is charged when the caller is refused, so a blocked client cannot
    push its own window further out.
    """
    now = monotonic()
    _prune(_anon_global_hits, now - ANON_GLOBAL_WINDOW_S)
    if len(_anon_global_hits) >= ANON_GLOBAL_LIMIT:
        return (
            "this demo's shared hourly limit is used up; add an access token "
            "for unlimited use"
        )

    log = _anon_hits[ip]
    _prune(log, now - ANON_RATE_WINDOW_S)
    if len(log) >= ANON_RATE_LIMIT:
        return (
            f"rate limit: {ANON_RATE_LIMIT} requests per "
            f"{ANON_RATE_WINDOW_S // 60} minutes without an access token"
        )

    log.append(now)
    _anon_global_hits.append(now)
    _sweep_stale_ip_windows(now)
    return None


# Sweeping every request would be O(number of IPs) per request. Amortize it.
_SWEEP_EVERY = 500
_sweep_countdown = _SWEEP_EVERY


def _sweep_stale_ip_windows(now: float) -> None:
    """Drop per-IP windows whose hits have all expired.

    Pruning is lazy and per-IP, so an IP that never returns keeps a deque
    holding expired timestamps forever: one entry per IP that ever visited,
    which is an unbounded leak in a long-lived process. Deleting only ALREADY
    empty deques does not fix it, because nothing empties them. Prune first,
    then delete.
    """
    global _sweep_countdown
    _sweep_countdown -= 1
    if _sweep_countdown > 0:
        return
    _sweep_countdown = _SWEEP_EVERY
    cutoff = now - ANON_RATE_WINDOW_S
    for key in list(_anon_hits):
        _prune(_anon_hits[key], cutoff)
        if not _anon_hits[key]:
            del _anon_hits[key]


# --- Security: the access token that lifts the anonymous limits ---
# The token is a BUDGET LIFT, not a wall. The UI shell (GET / and /static/*) is
# always open, and an anonymous visitor gets a working app under the limits
# above, because this is a capability demo and a dead UI demos nothing.
#
# The SocketIO stream is metered exactly like the HTTP API, and DO NOT exempt it
# on the grounds that "only our own page opens it." The browser is
# attacker-controlled, so there is no way to authenticate the page as distinct
# from a user: anyone who loads it can read its network calls and replay them.
# Mic streaming spends the key over that socket, so an unmetered socket is an
# unbounded key-spend path straight through the open UI.
#
# APP_ACCESS_TOKEN unset means everyone is privileged, which is what local dev
# and the test suite want. REQUIRE_AUTH=true asserts a token IS configured, so a
# deployment cannot silently come up with its limits disabled.
APP_ACCESS_TOKEN = os.getenv("APP_ACCESS_TOKEN", "").strip()
REQUIRE_AUTH = os.getenv("REQUIRE_AUTH", "").strip().lower() in ("1", "true", "yes", "on")

if REQUIRE_AUTH and not APP_ACCESS_TOKEN:
    raise RuntimeError(
        "REQUIRE_AUTH is set but APP_ACCESS_TOKEN is empty. Set the token "
        "(fly secrets set APP_ACCESS_TOKEN=...) or unset REQUIRE_AUTH to run "
        "with no privileged tier."
    )
if not APP_ACCESS_TOKEN:
    logger.warning(
        "APP_ACCESS_TOKEN is not set: every caller is privileged and the "
        "anonymous rate limits do not apply."
    )


def _extract_token(request: Request) -> str:
    """Pull the caller's token from a header or the query string.

    Query-string support is not laziness: an <audio src="/files/..."> element
    cannot send headers, so file playback has no other way to authenticate.
    """
    header = request.headers.get("x-app-token", "")
    if header:
        return header.strip()
    bearer = request.headers.get("authorization", "")
    if bearer.lower().startswith("bearer "):
        return bearer[7:].strip()
    return (request.query_params.get("token") or "").strip()


def _is_privileged(token: str) -> bool:
    """Does this token lift the anonymous limits?

    With no APP_ACCESS_TOKEN configured everyone is privileged, which is what
    local dev and the test suite want. compare_digest, not ==, so a wrong token
    cannot be recovered by timing.
    """
    if not APP_ACCESS_TOKEN:
        return True
    return secrets.compare_digest(token, APP_ACCESS_TOKEN)


def _enforce_access(request: Request) -> None:
    """Dependency on every endpoint that spends the Deepgram key.

    Privileged callers pass through untouched. Anonymous callers are allowed but
    metered, so the demo keeps working for a visitor with no token. Sets
    request.state.privileged so handlers can pick the right per-request caps.
    """
    privileged = _is_privileged(_extract_token(request))
    request.state.privileged = privileged
    if privileged:
        return
    if not ANON_ACCESS:
        raise HTTPException(
            status_code=401,
            detail="this instance requires an access token (send X-App-Token or ?token=)",
        )
    reason = _anon_rate_limit(_client_ip(request))
    if reason:
        raise HTTPException(status_code=429, detail=reason)


def _tts_char_cap(request: Request) -> int:
    return MAX_TTS_CHARS if getattr(request.state, "privileged", False) else ANON_MAX_TTS_CHARS


def _upload_byte_cap(request: Request) -> int:
    return MAX_UPLOAD_BYTES if getattr(request.state, "privileged", False) else ANON_MAX_UPLOAD_BYTES


async def _check_remote_audio_size(url: str, limit: int) -> str | None:
    """Bound the size of a caller-supplied audio URL before Deepgram fetches it.

    Uploads are capped as they stream, but a URL hands Deepgram an object of
    unknown length: the per-request rate limit cannot bound it, because one
    allowed request can point at a ten-hour recording. HEAD it first and require
    a Content-Length that fits. Returns None when acceptable, else a reason.

    An unknown length is refused rather than allowed: failing open here would
    make the whole cap decorative, since omitting Content-Length is trivial.
    """
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.head(url)
    except httpx.HTTPError as e:
        return f"could not check the audio URL: {e}"

    if resp.status_code >= 400:
        return f"the audio URL returned HTTP {resp.status_code}"
    length = resp.headers.get("content-length")
    if not length or not length.isdigit():
        return (
            "the audio URL did not report a size; upload the file instead, "
            "or add an access token"
        )
    if int(length) > limit:
        return f"the audio URL is larger than {limit} bytes"
    return None


class HostNotAllowed(ValueError):
    """Caller-supplied base_url is malformed or not in ALLOWED_STT_HOSTS."""


def _resolve_stt_host(params: dict) -> str:
    """Validate a caller-supplied base_url against the operator allowlist.

    Returns a bare host safe to interpolate into an outbound URL that carries
    the server's Deepgram credential. Raises HostNotAllowed otherwise.
    """
    raw = (params.get("base_url") or DEEPGRAM_HOST).strip().lower()
    if not _BARE_HOST_RE.match(raw):
        raise HostNotAllowed(
            "base_url must be a bare hostname with optional port, "
            "no scheme, path, query, or credentials"
        )
    if raw not in ALLOWED_STT_HOSTS:
        raise HostNotAllowed(f"base_url host is not allowlisted: {raw}")
    return raw


def _safe_temp_path(filename: str) -> Path:
    """Resolve an upload filename to a path guaranteed to sit inside TEMP_DIR.

    DO NOT join a caller-supplied filename onto TEMP_DIR directly. Python's
    Path join REPLACES the base when the right side is absolute, so
    TEMP_DIR / "/etc/passwd" is "/etc/passwd", and "../" components traverse
    out. Both were verified exploitable as arbitrary file writes 2026-07-25.
    """
    name = Path(filename or "").name
    if not name or name in (".", "..") or name.startswith("."):
        raise ValueError("invalid filename")
    path = (TEMP_DIR / name).resolve()
    if path.parent != TEMP_DIR.resolve():
        raise ValueError("resolved path escapes the temp directory")
    return path

# 1. AsyncServer — async_mode MUST be "asgi" (not "gevent", not "threading")
sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins="*",
)

# 2. FastAPI sub-app for HTTP routes only
fastapi_app = FastAPI()
fastapi_app.mount("/static", StaticFiles(directory="static"), name="static")

# 3. Combined ASGI callable — THIS is what uvicorn serves, not fastapi_app
app = socketio.ASGIApp(sio, fastapi_app)

def _clean_error(e: Exception) -> str:
    """Strip SDK request headers (including auth token) from Deepgram exception messages."""
    msg = str(e)
    m = re.search(r'status_code:\s*(\d+),\s*body:\s*(.+)$', msg, re.DOTALL)
    if m:
        return f"Deepgram {m.group(1)}: {m.group(2).strip()}"
    return msg


# Per-session state — module-level dict, not sio.session() (too slow for audio hot path)
# Key: SocketIO session id (sid)
# Value: dict with keys: task (asyncio.Task), stop_event (asyncio.Event),
#        ws (AsyncV1SocketClient | None), request_id (str | None)
_sessions: dict[str, dict] = {}

# Access tier per CONNECTED socket, which outlives any single stream on it.
# Kept separate from _sessions because _sessions only exists while a stream runs,
# and the tier has to be known at stream-start time to pick the limits.
_socket_tiers: dict[str, bool] = {}


# --- HTTP Routes ---

@fastapi_app.get("/")
async def index():
    return FileResponse("templates/index.html")


@fastapi_app.post("/upload", dependencies=[Depends(_enforce_access)])
async def upload(request: Request, file: UploadFile = File(...)):
    try:
        path = _safe_temp_path(file.filename)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    limit = _upload_byte_cap(request)
    # Stream to disk with a running total. DO NOT go back to `await file.read()`
    # then checking len(): that buffers the WHOLE upload in RAM before the check,
    # so an oversized body OOMs a 512mb machine before the cap can reject it.
    written = 0
    try:
        with path.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > limit:
                    out.close()
                    path.unlink(missing_ok=True)
                    return JSONResponse(
                        {"error": f"file exceeds {limit} bytes"}, status_code=413
                    )
                out.write(chunk)
    except OSError as e:
        path.unlink(missing_ok=True)
        return JSONResponse({"error": f"could not store upload: {e}"}, status_code=500)

    # Report the sanitized name; the caller must use it for follow-up calls.
    return JSONResponse({"filename": path.name, "size": written})


@fastapi_app.get("/files/{filename}", dependencies=[Depends(_enforce_access)])
async def serve_file(filename: str):
    try:
        path = _safe_temp_path(filename)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if not path.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path)


async def _tts_generate(text: str, tts_model: str, api_key: str) -> bytes:
    """Call Deepgram TTS and return MP3 bytes."""
    headers = {"Authorization": f"Token {api_key}"}
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            "https://api.deepgram.com/v1/speak",
            headers={**headers, "Content-Type": "application/json"},
            params={"model": tts_model, "encoding": "mp3"},
            json={"text": text},
        )
        resp.raise_for_status()
        return resp.content


async def _elevenlabs_tts_generate(text: str, voice_id: str, api_key: str) -> bytes:
    """Call ElevenLabs TTS and return MP3 bytes."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            json={
                "text": text,
                "model_id": "eleven_multilingual_v2",
            },
        )
        resp.raise_for_status()
        return resp.content


async def _stt_batch(audio_bytes: bytes, stt_params: dict, api_key: str) -> dict:
    """Transcribe audio bytes via Deepgram pre-recorded (batch) API."""
    headers = {"Authorization": f"Token {api_key}"}
    base_url = _resolve_stt_host(stt_params)
    query_params = serialize_params(stt_params, Mode.BATCH)
    query_params.setdefault("model", "nova-2")

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"https://{base_url}/v1/listen",
            headers={**headers, "Content-Type": "audio/mp3"},
            params=query_params,
            content=audio_bytes,
        )
        resp.raise_for_status()
        return resp.json()


async def _stt_streaming(text: str, tts_model: str, stt_params: dict, api_key: str) -> dict:
    """Pipe Deepgram TTS streaming response directly into the STT WebSocket.
    TTS audio chunks are forwarded to STT as they arrive — naturally paced at
    speech speed, no buffering or artificial timing needed.
    Returns {"transcript": str, "segments": list, "raw_responses": list}.
    """
    base_url = _resolve_stt_host(stt_params)
    headers = {"Authorization": f"Token {api_key}"}

    # For custom endpoints (e.g. aiworks), use raw websockets instead of the SDK
    if base_url != DEEPGRAM_HOST:
        return await _stt_streaming_raw(text, tts_model, stt_params, api_key)

    dg = AsyncDeepgramClient(api_key=api_key)
    sdk_kwargs = _params_to_sdk_kwargs(stt_params)

    segments = []

    async with dg.listen.v1.connect(**sdk_kwargs) as ws:
        async def on_message(msg, **kwargs):
            if isinstance(msg, ListenV1Results) and bool(msg.is_final):
                t = msg.channel.alternatives[0].transcript
                if t.strip():
                    segments.append(t)

        ws.on(EventType.MESSAGE, on_message)
        listen_task = asyncio.create_task(ws.start_listening())

        # Stream TTS audio directly into STT WebSocket as chunks arrive
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream(
                "POST",
                "https://api.deepgram.com/v1/speak",
                headers={**headers, "Content-Type": "application/json"},
                params={"model": tts_model, "encoding": "mp3"},
                json={"text": text},
            ) as tts_resp:
                tts_resp.raise_for_status()
                async for chunk in tts_resp.aiter_bytes(chunk_size=4096):
                    await ws.send_media(chunk)

        await ws.send_close_stream()
        await listen_task

    return {
        "transcript": " ".join(segments),
        "segments": segments,
    }


async def _stt_streaming_raw(text: str, tts_model: str, stt_params: dict, api_key: str) -> dict:
    """Stream TTS audio into a custom WebSocket STT endpoint (e.g. aiworks).
    Uses raw websockets since the Deepgram SDK only connects to api.deepgram.com.
    Returns full raw responses so callers can inspect the response schema.
    """
    base_url = _resolve_stt_host(stt_params)
    qs = query_string(stt_params, Mode.STREAMING)
    ws_url = f"wss://{base_url}/v1/listen?{qs}" if qs else f"wss://{base_url}/v1/listen"
    headers = {"Authorization": f"Token {api_key}"}

    # Generate TTS audio first (full buffer), then stream into WebSocket
    async with httpx.AsyncClient(timeout=60.0) as client:
        tts_resp = await client.post(
            "https://api.deepgram.com/v1/speak",
            headers={**headers, "Content-Type": "application/json"},
            params={"model": tts_model, "encoding": "mp3"},
            json={"text": text},
        )
        tts_resp.raise_for_status()
        audio_bytes = tts_resp.content

    segments = []
    raw_responses = []

    async with websockets.connect(ws_url, additional_headers=headers) as ws:
        # Send audio in chunks
        chunk_size = 4096
        for i in range(0, len(audio_bytes), chunk_size):
            await ws.send(audio_bytes[i:i + chunk_size])
            await asyncio.sleep(0.01)

        # Signal end of audio
        await ws.send(json_mod.dumps({"type": "CloseStream"}))

        # Collect all responses until connection closes
        try:
            async for msg in ws:
                if isinstance(msg, str):
                    data = json_mod.loads(msg)
                    raw_responses.append(data)
                    # Extract transcript from various response shapes
                    if "deepgram_stt" in data:
                        stt = data["deepgram_stt"]
                        if isinstance(stt, list):
                            stt = stt[0]
                        t = stt.get("channel", {}).get("alternatives", [{}])[0].get("transcript", "")
                        is_final = stt.get("is_final", False)
                        if t.strip() and is_final:
                            segments.append(t)
                    elif data.get("type") == "Results":
                        t = data.get("channel", {}).get("alternatives", [{}])[0].get("transcript", "")
                        if t.strip() and data.get("is_final"):
                            segments.append(t)
                    elif data.get("type") == "text":
                        t = data.get("data", "")
                        if t.strip():
                            segments.append(t)
                    # Top-level transcript (aiworks wrapper)
                    elif "transcript" in data and not any(k in data for k in ("deepgram_stt", "type")):
                        t = data["transcript"]
                        if t.strip():
                            segments.append(t)
        except websockets.exceptions.ConnectionClosed:
            pass

    return {
        "transcript": " ".join(segments),
        "segments": segments,
        "raw_responses": raw_responses,
    }


@fastapi_app.get("/api/tts-voices", dependencies=[Depends(_enforce_access)])
async def tts_voices(provider: str = "elevenlabs", language: str = ""):
    """Return available voices for a TTS provider, optionally filtered by language.

    For ElevenLabs, fetches both the user's own voices and popular shared
    voices for the requested language (via the shared-voices library).
    """
    if provider == "elevenlabs":
        api_key = os.getenv("ELEVENLABS_API_KEY", "")
        if not api_key:
            return JSONResponse({"error": "ELEVENLABS_API_KEY not set"}, status_code=500)

        def _normalize_voice(v, source="user"):
            labels = v.get("labels") or {}
            return {
                "voice_id": v["voice_id"],
                "name": v["name"],
                "language": labels.get("language", v.get("language", "")),
                "accent": labels.get("accent", v.get("accent", "")),
                "gender": labels.get("gender", v.get("gender", "")),
                "age": labels.get("age", ""),
                "description": labels.get("description", v.get("description", "")),
                "use_case": labels.get("use_case", v.get("use_case", "")),
                "source": source,
            }

        async with httpx.AsyncClient(timeout=30.0) as client:
            # Always fetch user's own voices
            resp = await client.get(
                "https://api.elevenlabs.io/v1/voices",
                headers={"xi-api-key": api_key},
            )
            resp.raise_for_status()
            user_voices = [
                _normalize_voice(v, "user")
                for v in resp.json().get("voices", [])
            ]

            # If a language is specified, also fetch shared voices for it
            shared_voices = []
            if language:
                shared_resp = await client.get(
                    "https://api.elevenlabs.io/v1/shared-voices",
                    params={"page_size": 20, "language": language},
                    headers={"xi-api-key": api_key},
                )
                if shared_resp.status_code == 200:
                    shared_voices = [
                        _normalize_voice(v, "shared")
                        for v in shared_resp.json().get("voices", [])
                    ]

        # Deduplicate (user voices take priority)
        seen_ids = {v["voice_id"] for v in user_voices}
        combined = user_voices + [v for v in shared_voices if v["voice_id"] not in seen_ids]
        return JSONResponse({"voices": combined})
    return JSONResponse({"error": f"Unknown provider: {provider}"}, status_code=400)


async def _generate_tts_audio(text: str, tts_model: str, provider: str) -> bytes:
    """Route TTS generation to the correct provider."""
    if provider == "elevenlabs":
        api_key = os.getenv("ELEVENLABS_API_KEY", "")
        if not api_key:
            raise ValueError("ELEVENLABS_API_KEY not set")
        return await _elevenlabs_tts_generate(text, tts_model, api_key)
    else:
        api_key = os.getenv("DEEPGRAM_API_KEY", "")
        return await _tts_generate(text, tts_model, api_key)


@fastapi_app.post("/api/tts-transcribe", dependencies=[Depends(_enforce_access)])
async def tts_transcribe(request: Request):
    body = await request.json()
    text = body.get("text", "").strip()
    tts_model = body.get("tts_model", "aura-2-asteria-en")
    tts_provider = body.get("tts_provider", "deepgram")
    stt_params = body.get("stt_params", {})
    mode = body.get("mode", "batch")  # "batch" | "streaming" | "both"

    if not text:
        return JSONResponse({"error": "text is required"}, status_code=400)
    tts_cap = _tts_char_cap(request)
    if len(text) > tts_cap:
        return JSONResponse(
            {"error": f"text exceeds {tts_cap} characters"}, status_code=413
        )
    if mode not in ("batch", "streaming", "both"):
        return JSONResponse({"error": "mode must be batch, streaming, or both"}, status_code=400)
    try:
        _resolve_stt_host(stt_params)
    except HostNotAllowed as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    api_key = os.getenv("DEEPGRAM_API_KEY", "")

    try:
        if mode == "batch":
            audio_bytes = await _generate_tts_audio(text, tts_model, tts_provider)
            result = await _stt_batch(audio_bytes, stt_params, api_key)
            return JSONResponse(result)

        elif mode == "streaming":
            if tts_provider == "elevenlabs":
                # ElevenLabs doesn't integrate with Deepgram streaming pipe,
                # so generate full audio first, then stream it to STT
                audio_bytes = await _generate_tts_audio(text, tts_model, tts_provider)
                result = await _stt_batch(audio_bytes, stt_params, api_key)
                return JSONResponse(result)
            result = await _stt_streaming(text, tts_model, stt_params, api_key)
            return JSONResponse(result)

        else:  # both
            if tts_provider == "elevenlabs":
                # Can't do streaming pipe with ElevenLabs, run batch only
                audio_bytes = await _generate_tts_audio(text, tts_model, tts_provider)
                result = await _stt_batch(audio_bytes, stt_params, api_key)
                return JSONResponse(result)

            async def _batch_pipeline():
                audio_bytes = await _generate_tts_audio(text, tts_model, tts_provider)
                return await _stt_batch(audio_bytes, stt_params, api_key)

            batch_result, stream_result = await asyncio.gather(
                _batch_pipeline(),
                _stt_streaming(text, tts_model, stt_params, api_key),
            )
            return JSONResponse({"batch": batch_result, "streaming": stream_result})

    except httpx.HTTPStatusError as e:
        return JSONResponse({"error": str(e)}, status_code=e.response.status_code)
    except Exception as e:
        return JSONResponse({"error": _clean_error(e)}, status_code=500)


@fastapi_app.post("/transcribe", dependencies=[Depends(_enforce_access)])
async def transcribe(request: Request):
    body = await request.json()
    params = body.get("params", {})
    url = body.get("url")
    filename = body.get("filename")

    if not url and not filename:
        return JSONResponse({"error": "url or filename required"}, status_code=400)

    api_key = os.getenv("DEEPGRAM_API_KEY", "")
    try:
        base_url = _resolve_stt_host(params)
    except HostNotAllowed as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    # An anonymous caller may not hand Deepgram an object of unknown length.
    if url and not getattr(request.state, "privileged", False):
        reason = await _check_remote_audio_size(url, _upload_byte_cap(request))
        if reason:
            return JSONResponse({"error": reason}, status_code=413)

    # Build clean query params for batch mode, convert bools to lowercase strings
    query_params = serialize_params(params, Mode.BATCH)
    query_params.setdefault("model", "nova-2")

    headers = {"Authorization": f"Token {api_key}"}
    listen_url = f"https://{base_url}/v1/listen"

    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            if url:
                resp = await client.post(
                    listen_url,
                    headers={**headers, "Content-Type": "application/json"},
                    params=query_params,
                    json={"url": url},
                )
            else:
                file_path = TEMP_DIR / filename
                if not file_path.exists():
                    return JSONResponse({"error": "File not found"}, status_code=404)
                file_bytes = file_path.read_bytes()
                resp = await client.post(
                    listen_url,
                    headers={**headers, "Content-Type": "audio/*"},
                    params=query_params,
                    content=file_bytes,
                )
            resp.raise_for_status()
            return JSONResponse(resp.json())
    except httpx.HTTPStatusError as e:
        return JSONResponse({"error": _clean_error(e)}, status_code=e.response.status_code)
    except Exception as e:
        return JSONResponse({"error": _clean_error(e)}, status_code=500)


# --- Helper functions ---

# Parameter names deepgram-sdk's connect() actually enumerates as keywords.
# Computed from the installed SDK, never hardcoded: the set grows between SDK
# releases, and a hardcoded copy silently rots into the bug below.
_SDK_CONNECT_KWARGS = frozenset(
    inspect.signature(AsyncDeepgramClient(api_key="_").listen.v1.connect).parameters
) - {"self", "request_options"}


def _params_to_sdk_kwargs(raw_params: dict) -> dict:
    """Convert a frontend params dict into deepgram-sdk 6.x connect() arguments.

    Splits into two buckets, because the SDK enumerates only ~28 of Deepgram's
    query parameters as keywords and raises TypeError on anything else:

        AsyncV1Client.connect() got an unexpected keyword argument 'keyterms'

    That killed the stream for `keyterms`, and equally for `filler_words`,
    `no_delay`, `word_confidence`, `alternatives`, `diarize_version` and
    `entity_prompt`, all of which are real Deepgram params the UI exposes.
    Anything the SDK does not name is forwarded verbatim through
    RequestOptions.additional_query_parameters, so it still reaches the wire.

    DO NOT "fix" a future occurrence by deleting the param from the UI. Add it
    to nothing: the split handles unknown names automatically, and widens on its
    own when a newer SDK starts naming them.

    model is required by connect(), so default it.
    """
    serialized = serialize_params(raw_params, Mode.STREAMING)
    serialized.setdefault("model", "nova-2")

    kwargs = {k: v for k, v in serialized.items() if k in _SDK_CONNECT_KWARGS}
    passthrough = {k: v for k, v in serialized.items() if k not in _SDK_CONNECT_KWARGS}
    if passthrough:
        logger.debug("params not named by the SDK, sent as query: %s", sorted(passthrough))
        kwargs["request_options"] = {"additional_query_parameters": passthrough}
    return kwargs


# --- Streaming Task ---

async def streaming_task(sid: str, params: dict, stop_event: asyncio.Event) -> None:
    """Owns the Deepgram WebSocket lifecycle for one SocketIO session.
    Runs as an asyncio.Task. Emits stream_started, transcription_update, stream_finished.
    """
    api_key = os.getenv("DEEPGRAM_API_KEY", "")
    dg = AsyncDeepgramClient(api_key=api_key)
    sdk_kwargs = _params_to_sdk_kwargs(params)

    try:
        async with dg.listen.v1.connect(**sdk_kwargs) as ws:
            # Store ws so on_audio_stream can call ws.send_media()
            if sid in _sessions:
                _sessions[sid]["ws"] = ws

            async def on_message(msg, **kwargs):
                logger.debug("[%s] on_message type=%s", sid, type(msg).__name__)
                if isinstance(msg, ListenV1Metadata):
                    if sid in _sessions:
                        _sessions[sid]["request_id"] = msg.request_id
                elif isinstance(msg, ListenV1Results):
                    transcript = msg.channel.alternatives[0].transcript
                    is_final = bool(msg.is_final)
                    await sio.emit("transcription_update", {
                        "transcript": transcript,
                        "is_final": is_final,
                    }, to=sid)

            ws.on(EventType.MESSAGE, on_message)
            listen_task = asyncio.create_task(ws.start_listening())

            # Emit stream_started immediately — don't gate on Metadata arrival
            await sio.emit("stream_started", {"request_id": None}, to=sid)

            # Keep-alive loop — sends every 8s (under Deepgram's ~10s idle timeout)
            async def keep_alive_loop():
                while not stop_event.is_set():
                    await asyncio.sleep(8)
                    if not stop_event.is_set():
                        try:
                            await ws.send_keep_alive()
                        except Exception as e:
                            logger.warning("[%s] keep_alive error: %s", sid, e)
                            break

            ka_task = asyncio.create_task(keep_alive_loop())

            # Wait for stop signal from on_toggle_transcription(stop) or
            # disconnect(). Anonymous streams also stop on a wall-clock cap:
            # an untokened mic stream left open is the largest unbounded spend
            # path in the app, and the per-request rate limit cannot bound it
            # because one connect can stream for hours.
            privileged = _sessions.get(sid, {}).get("privileged", False)
            if privileged:
                await stop_event.wait()
            else:
                try:
                    await asyncio.wait_for(
                        stop_event.wait(), timeout=ANON_MAX_STREAM_SECONDS
                    )
                except asyncio.TimeoutError:
                    logger.info("[%s] anon stream hit the %ss cap", sid, ANON_MAX_STREAM_SECONDS)
                    await sio.emit("stream_limit_reached", {
                        "reason": f"Demo streams stop after {ANON_MAX_STREAM_SECONDS} seconds. "
                                  f"Add an access token for unlimited streaming.",
                    }, to=sid)

            # Graceful shutdown: cancel keep-alive, send CloseStream, await final results
            ka_task.cancel()
            try:
                await ws.send_close_stream()
                await listen_task  # blocks until Deepgram flushes final Results + closes
            except (asyncio.CancelledError, Exception) as e:
                logger.warning("[%s] Error during graceful shutdown: %s", sid, e)
                if not listen_task.done():
                    listen_task.cancel()

    except Exception as e:
        logger.error("[%s] streaming_task error: %s", sid, e)
        _sessions.pop(sid, None)  # Free slot before notifying client so retries aren't blocked
        await sio.emit("stream_error", {"message": _clean_error(e)}, to=sid)
    finally:
        request_id = _sessions[sid].get("request_id") if sid in _sessions else None
        await sio.emit("stream_finished", {"request_id": request_id}, to=sid)
        _sessions.pop(sid, None)
        logger.info("[%s] streaming_task finished, session cleaned up", sid)


# --- File Streaming Task ---

CHUNK_SIZE = 4096


async def file_streaming_task(
    sid: str, filename: str, params: dict, stop_event: asyncio.Event
) -> None:
    """Streams an uploaded file to Deepgram over WebSocket.
    Mirrors streaming_task() but reads from a local file instead of waiting on stop_event.
    Emits stream_started, transcription_update, stream_finished.
    """
    api_key = os.getenv("DEEPGRAM_API_KEY", "")
    dg = AsyncDeepgramClient(api_key=api_key)
    sdk_kwargs = _params_to_sdk_kwargs(params)
    file_path = TEMP_DIR / filename

    try:
        async with dg.listen.v1.connect(**sdk_kwargs) as ws:
            # Store ws reference in session
            if sid in _sessions:
                _sessions[sid]["ws"] = ws

            async def on_message(msg, **kwargs):
                logger.debug("[%s] file on_message type=%s", sid, type(msg).__name__)
                if isinstance(msg, ListenV1Metadata):
                    if sid in _sessions:
                        _sessions[sid]["request_id"] = msg.request_id
                elif isinstance(msg, ListenV1Results):
                    transcript = msg.channel.alternatives[0].transcript
                    is_final = bool(msg.is_final)
                    await sio.emit("transcription_update", {
                        "transcript": transcript,
                        "is_final": is_final,
                        "start": msg.start,
                    }, to=sid)

            ws.on(EventType.MESSAGE, on_message)
            listen_task = asyncio.create_task(ws.start_listening())

            # Emit stream_started immediately — same pattern as streaming_task
            await sio.emit("stream_started", {"request_id": None}, to=sid)

            # Compute real-time pacing: sleep between chunks so Deepgram
            # receives audio at 1x speed, keeping transcripts in sync with playback.
            try:
                audio_info = MutagenFile(file_path)
                duration = audio_info.info.length if audio_info else None
            except Exception:
                duration = None
            file_size = file_path.stat().st_size
            sleep_per_chunk = (CHUNK_SIZE / file_size * duration) if duration and file_size else 0

            # Stream file in chunks; stop early if stop_event set
            try:
                with open(file_path, "rb") as f:
                    while not stop_event.is_set():
                        chunk = f.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        await ws.send_media(chunk)
                        if sleep_per_chunk:
                            await asyncio.sleep(sleep_per_chunk)
            except FileNotFoundError:
                await sio.emit("stream_error", {"message": f"File not found: {filename}"}, to=sid)
                # Graceful shutdown even on FileNotFoundError
                try:
                    await ws.send_close_stream()
                    await listen_task
                except (asyncio.CancelledError, Exception) as e:
                    logger.warning("[%s] Error during file-not-found shutdown: %s", sid, e)
                    if not listen_task.done():
                        listen_task.cancel()
                return

            # EOF reached (or stop_event set) — flush final words (STR-04 pattern)
            try:
                await ws.send_close_stream()
                await listen_task  # blocks until Deepgram flushes final Results + closes
            except (asyncio.CancelledError, Exception) as e:
                logger.warning("[%s] Error during file streaming graceful shutdown: %s", sid, e)
                if not listen_task.done():
                    listen_task.cancel()

    except Exception as e:
        logger.error("[%s] file_streaming_task error: %s", sid, e)
        _sessions.pop(sid, None)
        await sio.emit("stream_error", {"message": _clean_error(e)}, to=sid)
    finally:
        request_id = _sessions[sid].get("request_id") if sid in _sessions else None
        await sio.emit("stream_finished", {"request_id": request_id}, to=sid)
        _sessions.pop(sid, None)
        logger.info("[%s] file_streaming_task finished, session cleaned up", sid)


# --- SocketIO Event Handlers ---

@sio.event
async def connect(sid, environ, auth=None):
    """Gate the stream at connect time.

    Mic and file streaming both spend the Deepgram key over this socket, so it
    is part of the API surface even though the page that opens it is public.
    Refusing here is cheaper than checking on every audio frame.
    """
    supplied = ""
    if isinstance(auth, dict):
        supplied = str(auth.get("token") or "").strip()
    if not supplied:
        # Fall back to the handshake query string for non-browser clients.
        supplied = urllib.parse.parse_qs(
            environ.get("QUERY_STRING", "")
        ).get("token", [""])[0].strip()

    privileged = _is_privileged(supplied)
    if not privileged:
        if not ANON_ACCESS:
            logger.warning("Refused SocketIO connect (token required): %s", sid)
            raise socketio.exceptions.ConnectionRefusedError("access token required")
        anon_streams = sum(1 for s in _sessions.values() if not s.get("privileged"))
        if anon_streams >= ANON_MAX_CONCURRENT_STREAMS:
            logger.warning("Refused SocketIO connect (anon concurrency): %s", sid)
            raise socketio.exceptions.ConnectionRefusedError(
                "too many people are using the demo right now, try again shortly"
            )

    _socket_tiers[sid] = privileged
    logger.info("Client connected: %s privileged=%s", sid, privileged)
    # Tell the client which tier it is in so the UI can show its budget.
    await sio.emit("access_tier", {
        "privileged": privileged,
        "max_stream_seconds": None if privileged else ANON_MAX_STREAM_SECONDS,
        "max_tts_chars": MAX_TTS_CHARS if privileged else ANON_MAX_TTS_CHARS,
    }, to=sid)


@sio.event
async def disconnect(sid, reason=None):
    _socket_tiers.pop(sid, None)
    session = _sessions.pop(sid, None)
    if session:
        session["stop_event"].set()
        task = session.get("task")
        if task and not task.done():
            task.cancel()
    logger.info("Client disconnected: %s reason=%s", sid, reason)


@sio.on("toggle_transcription")
async def on_toggle_transcription(sid, data):
    action = data.get("action", "start")
    params = data.get("params", data.get("config", {}))
    logger.info("[%s] toggle_transcription action=%s", sid, action)

    if action == "start":
        if sid in _sessions:
            logger.warning("[%s] toggle_transcription(start) while already streaming — ignoring", sid)
            return
        stop_event = asyncio.Event()
        _sessions[sid] = {
            "stop_event": stop_event, "ws": None, "request_id": None,
            "privileged": _socket_tiers.get(sid, False),
        }
        task = asyncio.create_task(streaming_task(sid, params, stop_event))
        _sessions[sid]["task"] = task

    elif action == "stop":
        if sid not in _sessions:
            # Not streaming — keep frontend in sync
            await sio.emit("stream_finished", {"request_id": None}, to=sid)
            return
        _sessions[sid]["stop_event"].set()
        # stream_finished is emitted by streaming_task after listen_task completes


@sio.on("audio_stream")
async def on_audio_stream(sid, data):
    session = _sessions.get(sid)
    if session and session.get("ws") is not None:
        try:
            audio = data if isinstance(data, bytes) else bytes(data)
            await session["ws"].send_media(audio)
        except Exception as e:
            logger.warning("[%s] send_media error: %s", sid, e)
    # If ws is None (WebSocket not yet open), drop silently — browser buffers more audio


@sio.on("detect_audio_settings")
async def on_detect_audio_settings(sid):
    try:
        from common.audio_settings import detect_audio_settings
        settings = detect_audio_settings()
        await sio.emit("audio_settings", {
            "sample_rate": int(settings.get("sample_rate", 16000)),
            "channels": int(settings.get("max_input_channels", 1)),
        }, to=sid)
    except Exception as e:
        logger.warning("Audio settings detection failed: %s", e)
        await sio.emit("audio_settings", {"sample_rate": 16000, "channels": 1}, to=sid)


@sio.on("start_file_streaming")
async def on_start_file_streaming(sid, data):
    filename = data.get("filename") if data else None
    params = data.get("params", {}) if data else {}
    logger.info("[%s] start_file_streaming filename=%s", sid, filename)

    if not filename:
        await sio.emit("stream_error", {"message": "filename is required"}, to=sid)
        return

    if sid in _sessions:
        logger.warning("[%s] start_file_streaming while already streaming — ignoring", sid)
        return

    stop_event = asyncio.Event()
    _sessions[sid] = {
        "stop_event": stop_event, "ws": None, "request_id": None,
        "privileged": _socket_tiers.get(sid, False),
    }
    task = asyncio.create_task(file_streaming_task(sid, filename, params, stop_event))
    _sessions[sid]["task"] = task


@sio.on("stop_file_streaming")
async def on_stop_file_streaming(sid, data=None):
    logger.info("[%s] stop_file_streaming", sid)

    if sid not in _sessions:
        # Not streaming — keep frontend in sync
        await sio.emit("stream_finished", {"request_id": None}, to=sid)
        return

    _sessions[sid]["stop_event"].set()
    # stream_finished is emitted by file_streaming_task after listen_task completes
