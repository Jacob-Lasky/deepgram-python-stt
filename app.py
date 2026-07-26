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

from stt.options import (
    BATCH_ONLY,
    DENIED_PARAMS,
    FLUX_MULTILINGUAL_MODEL,
    FLUX_MULTILINGUAL_ONLY,
    FLUX_PARAMS,
    FLUX_RANGES,
    FLUX_REDACT_VALUES,
    INTERNAL_PARAMS,
    PARAM_ALIASES,
    REPEATABLE_PARAMS,
    STREAMING_ONLY,
    ZERO_MEANS_UNSET,
    Mode,
    flux_validation_error,
    is_flux_model,
    query_string,
    serialize_params,
)

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

# Anything that looks like a bearer credential in a string bound for the
# browser. `Token <key>` is how the SDK spells Deepgram auth in the request it
# echoes back inside its own exception messages.
_CREDENTIAL_RE = re.compile(r"\b(Token|Bearer)\s+\S+", re.IGNORECASE)


def _redact_credentials(text: str) -> str:
    """Strip bearer credentials from a string about to leave the server.

    Load-bearing, and NOT redundant with the parsing in _clean_error. That
    parsing only strips the Authorization header as a side effect of extracting
    the `status_code: N, body: ...` tail, so any SDK error WITHOUT that tail
    fell through to `return str(e)` with the key still in it. Verified: an
    ApiError reading

        headers: {'Authorization': 'Token <key>'}: connection refused

    returned the key verbatim to the browser, and /v2/listen handshake failures
    are exactly the shape that produces it.

    DO NOT drop this in favour of the regex above. Redaction has to be
    unconditional, because the leak lives in the path where parsing FAILED.
    """
    return _CREDENTIAL_RE.sub(r"\1 <redacted>", text)


def _clean_error(e: Exception) -> str:
    """Turn a Deepgram failure into the most specific message we can safely show.

    DO NOT weaken the header stripping below. SDK exception messages embed the
    full request, Authorization header included, so returning str(e) verbatim
    would put the server's API key in an HTTP response body.

    For httpx failures we substitute Deepgram's RESPONSE body, which is where the
    actual reason lives. str(HTTPStatusError) is only
        Client error '400 Bad Request' for url '...'
        For more information check: https://developer.mozilla.org/...
    which names neither the offending parameter nor the reason, and an MDN link
    to "what is a 400" is worse than useless in a diagnostic tool: the whole
    point of this app is to tell someone WHY Deepgram rejected their request.
    A response body is safe to surface — credentials travel in request headers.
    """
    if isinstance(e, httpx.HTTPStatusError):
        status = e.response.status_code
        try:
            body = e.response.json()
            detail = body.get("err_msg") or body.get("error") or body.get("message")
            code = body.get("err_code")
            if detail:
                # Deepgram often prefixes err_msg with err_code already
                # ("Bad Request: Nova-3 models do not support..."), so appending
                # it unconditionally reads as "... (Bad Request)" twice.
                if code and str(code).lower() not in str(detail).lower():
                    return f"Deepgram {status}: {detail} ({code})"
                return f"Deepgram {status}: {detail}"
            return f"Deepgram {status}: {json_mod.dumps(body)[:500]}"
        except ValueError:
            text = (e.response.text or "").strip()
            return f"Deepgram {status}: {text[:500]}" if text else f"Deepgram {status}"

    if isinstance(e, websockets.exceptions.ConnectionClosed):
        # Deepgram rejects an unsupported parameter on a STREAM by completing the
        # handshake and then closing with code 1000 and NO reason, so websockets
        # reports only "received 1000 (OK); then sent 1000 (OK)". That names
        # neither the parameter nor the cause, and it reads like a clean shutdown
        # rather than a rejection. Verified live: alternatives=2 on nova-3 closes
        # this way, while the same request in batch mode returns a 400.
        return (
            f"Deepgram closed the stream immediately without transcribing ({e}). "
            "A close with no reason is what an unsupported parameter looks like on "
            "a stream — most often a parameter the chosen model does not accept "
            "(e.g. alternatives>1 on nova-*, which is valid on base and enhanced). "
            "Re-run the same parameters in batch mode: there Deepgram returns a "
            "400 stating the actual reason."
        )

    msg = _redact_credentials(str(e))
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


@fastapi_app.get("/api/param-gating")
async def param_gating():
    """Serve stt/options.py's gating rules to the browser.

    The frontend used to keep its own copies of the mode lists, hardcoded in two
    functions, and they had drifted: the JS lists were missing channels,
    encoding, sample_rate, filler_words, measurements, utt_split and
    detect_language, so the URL bar showed a request that was not the request
    being sent. In an app whose headline feature is "here is the exact URL,
    copy it into curl", a URL that lies is the worst possible bug.

    options.py calls itself the single parameter gate. This endpoint is what
    makes that true rather than aspirational. DO NOT reintroduce a copy of any
    of these lists in JavaScript.

    Deliberately unauthenticated: it is a description of Deepgram's public API
    surface, it spends nothing, and the params panel has to render for the
    anonymous visitors this app exists to demo to.
    """
    return {
        "streaming_only": sorted(STREAMING_ONLY),
        "batch_only": sorted(BATCH_ONLY),
        "internal": sorted(INTERNAL_PARAMS),
        "denied": sorted(DENIED_PARAMS),
        "aliases": PARAM_ALIASES,
        "repeatable": sorted(REPEATABLE_PARAMS),
        "zero_means_unset": sorted(ZERO_MEANS_UNSET),
        "flux": {
            "params": sorted(FLUX_PARAMS),
            "multilingual_only": sorted(FLUX_MULTILINGUAL_ONLY),
            "multilingual_model": FLUX_MULTILINGUAL_MODEL,
            "ranges": {k: list(v) for k, v in FLUX_RANGES.items()},
            "redact_values": sorted(FLUX_REDACT_VALUES),
        },
    }


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
    turns = _TurnTracker()

    async with _connect_stream(dg, sdk_kwargs) as ws:
        async def on_message(msg, **kwargs):
            # _transcript_event is the same parser the socket path uses, so a
            # Flux turn counts as a segment here on exactly the same rule
            # (EndOfTurn only) that makes it a final line in the browser.
            payload = _transcript_event(msg)
            if payload is None:
                return
            turns.observe(payload)
            if payload["is_final"] and payload["transcript"].strip():
                segments.append(payload["transcript"])

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

    result = {
        "transcript": " ".join(segments),
        "segments": segments,
    }
    if turns.pending:
        # This is the sweep path that writes CSVs, so a silently short Flux
        # transcript here becomes a data point someone later cites as a model
        # result. It has to arrive labelled. Same cause as the browser's
        # stream_notice; see _unfinished_turn_notice.
        result["unfinalized_transcript"] = turns.pending
        result["notice"] = _unfinished_turn_notice(turns.pending)["message"]
    return result


async def _stt_streaming_raw(text: str, tts_model: str, stt_params: dict, api_key: str) -> dict:
    """Stream TTS audio into a custom WebSocket STT endpoint (e.g. aiworks).
    Uses raw websockets since the Deepgram SDK only connects to api.deepgram.com.
    Returns full raw responses so callers can inspect the response schema.
    """
    base_url = _resolve_stt_host(stt_params)
    qs = query_string(stt_params, Mode.STREAMING)
    # Flux lives on a different path. A custom endpoint is unlikely to serve it,
    # but hardcoding /v1/listen for a flux-* model would send the request
    # somewhere it certainly is not, and the failure would look like the
    # endpoint's fault rather than the model's.
    path = "/v2/listen" if is_flux_model(stt_params.get("model")) else "/v1/listen"
    ws_url = f"wss://{base_url}{path}?{qs}" if qs else f"wss://{base_url}{path}"
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
                    turn = _transcript_event(data)
                    if turn is not None:
                        # Flux TurnInfo, same EndOfTurn-only rule as everywhere else.
                        if turn["is_final"] and turn["transcript"].strip():
                            segments.append(turn["transcript"])
                    elif "deepgram_stt" in data:
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
        # _clean_error, never str(e): it surfaces Deepgram's reason and strips
        # request headers. This handler used str(e) and returned an MDN link.
        return JSONResponse({"error": _clean_error(e)}, status_code=e.response.status_code)
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

# The two endpoints name different keywords, so the split below has to know
# which one the request is bound for. v2 names none of v1's formatting params
# and adds the end-of-turn knobs.
_FLUX_CONNECT_KWARGS = frozenset(
    inspect.signature(AsyncDeepgramClient(api_key="_").listen.v2.connect).parameters
) - {"self", "request_options"}


def _params_to_sdk_kwargs(raw_params: dict) -> dict:
    """Convert a frontend params dict into deepgram-sdk connect() arguments.

    Splits into two buckets, because the SDK enumerates only some of Deepgram's
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
    named = (
        _FLUX_CONNECT_KWARGS
        if is_flux_model(serialized.get("model"))
        else _SDK_CONNECT_KWARGS
    )

    kwargs = {k: v for k, v in serialized.items() if k in named}
    passthrough = {k: v for k, v in serialized.items() if k not in named}
    if passthrough:
        logger.debug("params not named by the SDK, sent as query: %s", sorted(passthrough))
        kwargs["request_options"] = {"additional_query_parameters": passthrough}
    return kwargs


def _connect_stream(dg: AsyncDeepgramClient, sdk_kwargs: dict):
    """Open the stream on the endpoint the model implies.

    Flux is not another model on /v1/listen, it is /v2/listen. Sending a Flux
    model to v1 is a 400 that says so plainly
    (`V2_MODEL_ON_V1_LISTEN_ENDPOINT`), which is why nothing here guards
    against it: Deepgram's own message is better than one we would write, and
    duplicating it would be a second source of truth to keep in sync.
    """
    if is_flux_model(sdk_kwargs.get("model")):
        return dg.listen.v2.connect(**sdk_kwargs)
    return dg.listen.v1.connect(**sdk_kwargs)


def _as_dict(msg) -> dict:
    """Normalise one stream message to a plain dict, whichever endpoint sent it.

    DO NOT replace this with isinstance checks against the v2 types. On
    /v2/listen they are never true. The SDK declares

        V2SocketClientResponse = Union[ListenV2Connected, ListenV2TurnInfo,
                                       Any, ListenV2ConfigureFailure,
                                       ListenV2FatalError]

    and that bare `Any` swallows everything: construct_type matches it first and
    hands back the parsed JSON untouched, so every Flux message arrives as a
    dict. The v1 union has no `Any`, so v1 messages arrive as models. Writing
    the Flux handler the way the v1 handler is written therefore drops every
    turn in silence. Measured against production with deepgram-sdk 7.6.0.

    Normalising instead of isinstance-ing also survives the fix: a later SDK
    that tightens the union starts sending models and this keeps working.
    """
    if isinstance(msg, dict):
        return msg
    dump = getattr(msg, "model_dump", None)
    if callable(dump):
        return dump()
    return dict(getattr(msg, "__dict__", {}) or {})


# The one Flux turn event that means "this transcript is settled". Everything
# else — Update, StartOfTurn, EagerEndOfTurn, TurnResumed — is the same turn
# still being revised, which is what `is_final: false` already means.
#
# EagerEndOfTurn is deliberately NOT final even though its transcript is
# guaranteed to match the EndOfTurn that follows it: a TurnResumed can land in
# between and extend the turn, and treating the eager event as final would
# commit a line the speaker had not finished.
FLUX_FINAL_EVENTS = {"EndOfTurn"}

# Flux reports a failed stream in-band instead of closing the socket.
FLUX_ERROR_TYPES = {"FatalError", "ConfigureFailure"}


def _transcript_event(msg) -> dict | None:
    """Parse one stream message into the transcription_update payload, or None.

    THE single parser for both endpoints. Returns None for messages that carry
    no transcript at all (Metadata, Connected, SpeechStarted), so callers can
    treat "no payload" as "nothing to show".

    The Flux fields below the first three are additive: the frontend renders a
    turn exactly like a v1 interim/final, and the responses pane gets the turn
    index and the end-of-turn confidence that are the whole reason to look at
    Flux in a diagnostic tool.
    """
    if isinstance(msg, ListenV1Results):
        return {
            "transcript": msg.channel.alternatives[0].transcript,
            "is_final": bool(msg.is_final),
            "start": msg.start,
        }

    d = _as_dict(msg)
    if d.get("type") != "TurnInfo":
        return None

    event = d.get("event")
    return {
        "transcript": d.get("transcript") or "",
        "is_final": event in FLUX_FINAL_EVENTS,
        "start": d.get("audio_window_start"),
        "flux_event": event,
        "turn_index": d.get("turn_index"),
        "end_of_turn_confidence": d.get("end_of_turn_confidence"),
        "audio_window_end": d.get("audio_window_end"),
        "words": d.get("words"),
        "languages": d.get("languages"),
    }


def _flux_error_message(d: dict) -> str:
    """Human-readable text for a Flux in-band failure.

    ListenV2FatalError carries `code` and `description`. ListenV2ConfigureFailure
    carries NEITHER — its only fields are type, request_id and sequence_id — so
    saying so beats dumping the raw JSON and leaving the reader to notice there
    is nothing in it.
    """
    kind = d.get("type")
    description = d.get("description") or d.get("message")
    code = d.get("code")
    if description:
        return f"Deepgram {kind}: {description}" + (f" ({code})" if code else "")
    if kind == "ConfigureFailure":
        return (
            "Deepgram rejected the mid-stream Configure message. It reports no "
            "reason for this — the message carries no description field — so "
            "check the thresholds and keyterms being sent against their "
            "documented ranges."
        )
    return f"Deepgram {kind}: {json_mod.dumps(d)[:500]}"


class _TurnTracker:
    """Remembers whether a Flux stream is sitting on an unfinalised turn.

    Shared by the socket path and the TTS round trip so both notice a truncated
    Flux result the same way. See _unfinished_turn_notice for why it matters:
    without this, the transcript silently stops short of the audio.
    """

    def __init__(self) -> None:
        self.pending: str | None = None

    def observe(self, payload: dict) -> None:
        if payload.get("flux_event") is None:
            return  # v1 has no turns, so it can never be mid-turn
        self.pending = (
            None if payload["is_final"] else (payload["transcript"] or "").strip() or None
        )


def _stream_message_handler(sid: str, turns: "_TurnTracker"):
    """Build the EventType.MESSAGE callback for one session, either endpoint."""

    async def on_message(msg, **kwargs):
        if isinstance(msg, ListenV1Metadata):
            if sid in _sessions:
                _sessions[sid]["request_id"] = msg.request_id
            return

        d = _as_dict(msg)
        kind = d.get("type")

        if kind == "Connected":
            # Flux's equivalent of v1 Metadata, and the only place the request
            # id shows up before the first turn.
            if sid in _sessions:
                _sessions[sid]["request_id"] = d.get("request_id")
            return

        if kind in FLUX_ERROR_TYPES:
            await sio.emit(
                "stream_error",
                {"message": _redact_credentials(_flux_error_message(d))},
                to=sid,
            )
            return

        payload = _transcript_event(msg)
        if payload is not None:
            turns.observe(payload)
            await sio.emit("transcription_update", payload, to=sid)

    return on_message


def _unfinished_turn_notice(pending: str) -> dict:
    """Explain a Flux transcript that stops short of the audio.

    Flux ends a turn on end-of-turn confidence, or after eot_timeout_ms of
    silence WITHIN THE AUDIO. CloseStream flushes turns that have already
    ended; it does NOT force the one in progress to end. So if the audio stops
    while someone is still mid-sentence, that sentence is never finalised and
    never appears in the transcript.

    Measured, because it is easy to misread as Flux dropping words. Same 19-word
    clip, 8 runs each: with a second of trailing silence, 8/8 returned all 19
    words; with the clip cut tight to the last syllable, 0/8 did — every run
    returned only the 8 words of the first completed turn.

    DO NOT "fix" this by injecting silence before CloseStream. It works for raw
    encodings and cannot work for containerised WebM/Ogg (there are no valid
    silent bytes to append), so it would quietly change behaviour depending on
    input format. Worse, it makes the tool transcribe audio the user did not
    supply, which is the same sin as silently dropping a parameter: the output
    stops describing the input. Report it instead.
    """
    return {
        # Short enough for a toast that dismisses itself; the full explanation
        # stays in the responses pane, which does not.
        "summary": "Stream ended mid-turn — the last turn is missing from the transcript.",
        "message": (
            "Stream ended mid-turn, so Flux never finalised the last one and it "
            "is not in the transcript above. Flux ends a turn on confidence, or "
            "after eot_timeout_ms of silence in the AUDIO — a stream that just "
            "stops leaves the turn open, and CloseStream does not force it shut. "
            "Give the audio a second of trailing silence, or lower eot_timeout_ms."
        ),
        "unfinalized_transcript": pending,
    }


# --- Streaming lifecycle ---
#
# streaming_task and file_streaming_task used to be two near-identical copies of
# the same 60 lines: connect, wire up the handler, announce, flush on close,
# report, clean up. The only genuinely different part is what drives the socket
# in the middle. They are one function now with that middle passed in, which is
# why adding the Flux endpoint was a change in one place rather than two that
# have to be kept in step.

CHUNK_SIZE = 4096

# Under Deepgram's ~10s idle timeout on /v1/listen.
KEEP_ALIVE_SECONDS = 8


def _start_keep_alive(sid: str, ws, stop_event: asyncio.Event, flux: bool):
    """Keep an idle v1 stream open. Returns None on Flux, which has no KeepAlive.

    /v2/listen has exactly TWO control messages, Configure and CloseStream. There
    is no KeepAlive anywhere in the Flux docs, and the v2 socket client has no
    send_keep_alive at all, so the v1 loop would kill a Flux stream with an
    AttributeError eight seconds in.

    DO NOT relax this to `hasattr(ws, "send_keep_alive")`. The constraint is
    that the ENDPOINT has no such control message, not that this SDK build
    happens not to expose the method; a later regeneration could add the method
    and the hasattr form would silently start sending something /v2/listen never
    agreed to accept.

    Flux does not need one from this app anyway: the only driver that can idle
    is the mic, and MediaRecorder keeps emitting frames through silence.
    """
    if flux:
        return None

    async def loop():
        while not stop_event.is_set():
            await asyncio.sleep(KEEP_ALIVE_SECONDS)
            if stop_event.is_set():
                return
            try:
                await ws.send_keep_alive()
            except Exception as e:
                logger.warning("[%s] keep_alive error: %s", sid, e)
                return

    return asyncio.create_task(loop())


async def _run_stream(sid: str, params: dict, drive) -> None:
    """Own the Deepgram WebSocket lifecycle for one SocketIO session.

    `drive(ws)` is the caller's half: the mic waits to be told to stop while
    on_audio_stream feeds the socket, the file reader pumps chunks. Everything
    else is the same for both, and for both endpoints.

    Emits stream_started, transcription_update, stream_error, stream_finished.
    """
    problem = flux_validation_error(params)
    if problem:
        # Deepgram would reject these too, but only with "Unexpected error when
        # initializing websocket connection", which names nothing. Refusing here
        # is the only way the user learns which knob is out of range.
        logger.info("[%s] refused before connecting: %s", sid, problem)
        await sio.emit("stream_error", {"message": problem}, to=sid)
        await sio.emit("stream_finished", {"request_id": None}, to=sid)
        _sessions.pop(sid, None)
        return

    dg = AsyncDeepgramClient(api_key=os.getenv("DEEPGRAM_API_KEY", ""))
    sdk_kwargs = _params_to_sdk_kwargs(params)
    turns = _TurnTracker()

    try:
        async with _connect_stream(dg, sdk_kwargs) as ws:
            # Store ws so on_audio_stream can call ws.send_media()
            if sid in _sessions:
                _sessions[sid]["ws"] = ws

            ws.on(EventType.MESSAGE, _stream_message_handler(sid, turns))
            listen_task = asyncio.create_task(ws.start_listening())

            # Emit stream_started immediately — don't gate on Metadata arrival
            await sio.emit("stream_started", {"request_id": None}, to=sid)

            try:
                await drive(ws, is_flux_model(sdk_kwargs.get("model")))
            finally:
                # Graceful shutdown: CloseStream, then wait for Deepgram to
                # flush its final results. In a finally because a stream that
                # died mid-drive still has buffered audio worth recovering.
                try:
                    await ws.send_close_stream()
                    await listen_task
                except (asyncio.CancelledError, Exception) as e:
                    logger.warning("[%s] error during graceful shutdown: %s", sid, e)
                    if not listen_task.done():
                        listen_task.cancel()

    except Exception as e:
        logger.error("[%s] stream error: %s", sid, e)
        _sessions.pop(sid, None)  # Free slot before notifying client so retries aren't blocked
        await sio.emit("stream_error", {"message": _clean_error(e)}, to=sid)
    finally:
        session = _sessions.get(sid) or {}
        if turns.pending:
            await sio.emit("stream_notice", _unfinished_turn_notice(turns.pending), to=sid)
        await sio.emit("stream_finished", {"request_id": session.get("request_id")}, to=sid)
        _sessions.pop(sid, None)
        logger.info("[%s] stream finished, session cleaned up", sid)


async def streaming_task(sid: str, params: dict, stop_event: asyncio.Event) -> None:
    """Microphone streaming. Audio arrives via on_audio_stream, so this only waits."""

    async def drive(ws, flux):
        ka_task = _start_keep_alive(sid, ws, stop_event, flux)
        try:
            # Wait for stop signal from on_toggle_transcription(stop) or
            # disconnect(). Anonymous streams also stop on a wall-clock cap:
            # an untokened mic stream left open is the largest unbounded spend
            # path in the app, and the per-request rate limit cannot bound it
            # because one connect can stream for hours.
            if _sessions.get(sid, {}).get("privileged", False):
                await stop_event.wait()
                return
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
        finally:
            if ka_task is not None:
                ka_task.cancel()

    await _run_stream(sid, params, drive)


async def file_streaming_task(
    sid: str, filename: str, params: dict, stop_event: asyncio.Event
) -> None:
    """Streams an uploaded file to Deepgram over WebSocket, paced at 1x."""
    file_path = TEMP_DIR / filename

    async def drive(ws, flux):
        # Sleep between chunks so Deepgram receives audio at real-time speed,
        # keeping transcripts in sync with playback.
        try:
            audio_info = MutagenFile(file_path)
            duration = audio_info.info.length if audio_info else None
        except Exception:
            duration = None
        try:
            file_size = file_path.stat().st_size
        except FileNotFoundError:
            await sio.emit("stream_error", {"message": f"File not found: {filename}"}, to=sid)
            return
        sleep_per_chunk = (CHUNK_SIZE / file_size * duration) if duration and file_size else 0

        with open(file_path, "rb") as f:
            while not stop_event.is_set():
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                await ws.send_media(chunk)
                if sleep_per_chunk:
                    await asyncio.sleep(sleep_per_chunk)

    await _run_stream(sid, params, drive)


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
