# tests/test_security.py
# Regression tests for this app's abuse surface. It holds a Deepgram key and
# calls Deepgram on a caller's behalf, so every request field is hostile input.
# The UI shell is public by design; the API and the SocketIO stream are gated.
# Each case here corresponds to something verified exploitable on 2026-07-25.
# Uses pytest-asyncio auto mode (no @pytest.mark.asyncio needed).
import os
from pathlib import Path

import pytest
from fastapi import HTTPException

os.environ.setdefault("DEEPGRAM_API_KEY", "test-key")

import app
from stt.options import clean_params, Mode


# ---------------------------------------------------------------------------
# base_url must never let a caller choose where the server's API key goes.
# Verified exploit: base_url="postman-echo.com/post?x=" delivered
# "Authorization: Token <key>" to that third-party host.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("evil", [
    "postman-echo.com/post?x=",      # path + query injection (the live exploit)
    "attacker.example",              # bare but not allowlisted
    "api.deepgram.com@attacker.tld",  # userinfo trick
    "api.deepgram.com/../attacker",  # traversal in the host slot
    "attacker.tld#api.deepgram.com",  # fragment trick
    "attacker.tld?a=api.deepgram.com",
    "https://attacker.tld",          # scheme smuggling
    "api.deepgram.com evil.tld",     # whitespace
    "api.deepgram.com:1234",         # allowlisted name, unexpected port
])
def test_base_url_rejects_untrusted_destinations(evil):
    with pytest.raises(app.HostNotAllowed):
        app._resolve_stt_host({"base_url": evil})


def test_base_url_defaults_to_deepgram():
    assert app._resolve_stt_host({}) == "api.deepgram.com"
    assert app._resolve_stt_host({"base_url": ""}) == "api.deepgram.com"
    assert app._resolve_stt_host({"base_url": "  API.DEEPGRAM.COM "}) == "api.deepgram.com"


def test_base_url_allows_an_operator_allowlisted_host(monkeypatch):
    """The allowlist is operator-controlled via env, never caller-controlled."""
    monkeypatch.setattr(app, "ALLOWED_STT_HOSTS", {"api.deepgram.com", "flow.aiworks.test"})
    assert app._resolve_stt_host({"base_url": "flow.aiworks.test"}) == "flow.aiworks.test"
    with pytest.raises(app.HostNotAllowed):
        app._resolve_stt_host({"base_url": "other.aiworks.test"})


# ---------------------------------------------------------------------------
# /upload must not write outside TEMP_DIR.
# Verified exploit: both an absolute filename and ../ traversal wrote to an
# arbitrary path, because Path join REPLACES the base on an absolute RHS.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("evil", [
    "/etc/passwd",
    "/tmp/escaped.txt",
    "../../../../etc/passwd",
    "../escaped.txt",
    "..",
    ".",
    "",
    None,
    ".ssh",
    "sub/dir/file.wav",
])
def test_upload_filename_cannot_escape_temp_dir(evil):
    try:
        path = app._safe_temp_path(evil)
    except ValueError:
        return  # rejected outright, which is also correct
    assert path.parent == app.TEMP_DIR.resolve(), f"{evil!r} escaped to {path}"


def test_safe_temp_path_keeps_a_normal_filename():
    assert app._safe_temp_path("sample.wav").name == "sample.wav"
    # A directory component is stripped, not rejected, so uploads still work.
    assert app._safe_temp_path("sub/dir/sample.wav").name == "sample.wav"


# ---------------------------------------------------------------------------
# callback is a server-side exfil primitive: it makes Deepgram POST the
# transcript to a URL the caller picked, on the server's account.
# ---------------------------------------------------------------------------

def test_callback_is_never_forwarded():
    out = clean_params({"model": "nova-3", "callback": "https://attacker.tld/c"}, Mode.BATCH)
    assert "callback" not in out


def test_callback_cannot_be_smuggled_through_extra():
    """The extra merge runs after the filter loop, so it needs its own guard."""
    out = clean_params(
        {"model": "nova-3", "extra": {"callback": "https://attacker.tld/c"}},
        Mode.BATCH,
    )
    assert "callback" not in out


def test_base_url_cannot_be_smuggled_through_extra():
    out = clean_params(
        {"model": "nova-3", "extra": {"base_url": "attacker.tld"}}, Mode.BATCH
    )
    assert "base_url" not in out


def test_extra_still_passes_ordinary_params():
    out = clean_params({"model": "nova-3", "extra": {"custom_flag": "1"}}, Mode.BATCH)
    assert out["custom_flag"] == "1"


# ---------------------------------------------------------------------------
# Spend and disk caps exist, because the app is unauthenticated.
# ---------------------------------------------------------------------------

def test_abuse_caps_are_configured():
    assert app.MAX_TTS_CHARS > 0
    assert app.MAX_UPLOAD_BYTES > 0


def test_no_api_key_in_static_assets():
    """The Deepgram key is server-side only; it must never reach the bundle."""
    root = Path(__file__).resolve().parents[1]
    for asset in list((root / "static").glob("*.js")) + [root / "templates" / "index.html"]:
        text = asset.read_text()
        assert "DEEPGRAM_API_KEY" not in text, f"key name leaked in {asset.name}"
        assert "ELEVENLABS_API_KEY" not in text, f"key name leaked in {asset.name}"


# ---------------------------------------------------------------------------
# API access token. The UI shell is deliberately public; everything that spends
# the Deepgram key is gated, SocketIO included.
# ---------------------------------------------------------------------------

def test_require_auth_without_a_token_is_a_hard_error():
    """A deployment must not be able to come up open because a secret was missed.

    Runs in a subprocess on purpose. importlib.reload(app) would rebind the
    module while conftest's session-scoped UvicornTestServer still holds the old
    ASGI callable and its sio instance, which silently breaks the socket tests.
    """
    import subprocess
    import sys

    env = {
        **os.environ,
        "REQUIRE_AUTH": "true",
        "DEEPGRAM_API_KEY": "test-key",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
    }
    env.pop("APP_ACCESS_TOKEN", None)
    proc = subprocess.run(
        [sys.executable, "-c", "import app"],
        capture_output=True, text=True, env=env,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert proc.returncode != 0, "app started with REQUIRE_AUTH and no token"
    assert "APP_ACCESS_TOKEN" in proc.stderr


def test_require_auth_with_a_token_starts_cleanly():
    import subprocess
    import sys

    env = {
        **os.environ,
        "REQUIRE_AUTH": "true",
        "APP_ACCESS_TOKEN": "a-token",
        "DEEPGRAM_API_KEY": "test-key",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
    }
    proc = subprocess.run(
        [sys.executable, "-c", "import app; assert app.APP_ACCESS_TOKEN == 'a-token'"],
        capture_output=True, text=True, env=env,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert proc.returncode == 0, proc.stderr


class _FakeRequest:
    """Minimal stand-in for starlette Request: headers, query_params, state."""

    class _State:
        pass

    def __init__(self, headers=None, query=None, client_ip="203.0.113.9"):
        self.headers = headers or {}
        self.query_params = query or {}
        self.state = self._State()
        self.client = type("C", (), {"host": client_ip})()


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """Rate-limit state is module-global; a leaked window breaks later tests."""
    app._anon_hits.clear()
    app._anon_global_hits.clear()
    yield
    app._anon_hits.clear()
    app._anon_global_hits.clear()


def test_everyone_is_privileged_when_no_token_is_configured():
    """Local dev and this suite run with the limits off."""
    assert app.APP_ACCESS_TOKEN == ""
    assert app._is_privileged("") is True
    req = _FakeRequest()
    assert app._enforce_access(req) is None
    assert req.state.privileged is True


@pytest.mark.parametrize("headers,query", [
    ({"x-app-token": "right-token"}, {}),
    ({"authorization": "Bearer right-token"}, {}),
    ({"authorization": "bearer right-token"}, {}),   # scheme is case-insensitive
    ({}, {"token": "right-token"}),                  # <audio src> has no headers
])
def test_a_valid_token_is_privileged(monkeypatch, headers, query):
    monkeypatch.setattr(app, "APP_ACCESS_TOKEN", "right-token")
    req = _FakeRequest(headers, query)
    assert app._enforce_access(req) is None
    assert req.state.privileged is True


@pytest.mark.parametrize("headers,query", [
    ({}, {}),
    ({"x-app-token": "wrong"}, {}),
    ({"authorization": "Bearer wrong"}, {}),
    ({}, {"token": "wrong"}),
])
def test_no_token_still_works_but_unprivileged(monkeypatch, headers, query):
    """The demo must keep working for a visitor with no token."""
    monkeypatch.setattr(app, "APP_ACCESS_TOKEN", "right-token")
    req = _FakeRequest(headers, query)
    assert app._enforce_access(req) is None, "anonymous access must be allowed"
    assert req.state.privileged is False


def test_anon_access_can_be_switched_off_entirely(monkeypatch):
    monkeypatch.setattr(app, "APP_ACCESS_TOKEN", "right-token")
    monkeypatch.setattr(app, "ANON_ACCESS", False)
    with pytest.raises(HTTPException) as exc:
        app._enforce_access(_FakeRequest())
    assert exc.value.status_code == 401


def test_anon_per_ip_rate_limit_returns_429(monkeypatch):
    monkeypatch.setattr(app, "APP_ACCESS_TOKEN", "right-token")
    monkeypatch.setattr(app, "ANON_RATE_LIMIT", 3)
    for _ in range(3):
        assert app._enforce_access(_FakeRequest(client_ip="198.51.100.7")) is None
    with pytest.raises(HTTPException) as exc:
        app._enforce_access(_FakeRequest(client_ip="198.51.100.7"))
    assert exc.value.status_code == 429
    assert "rate limit" in exc.value.detail


def test_a_second_ip_has_its_own_per_ip_budget(monkeypatch):
    monkeypatch.setattr(app, "APP_ACCESS_TOKEN", "right-token")
    monkeypatch.setattr(app, "ANON_RATE_LIMIT", 2)
    for _ in range(2):
        app._enforce_access(_FakeRequest(client_ip="198.51.100.1"))
    assert app._enforce_access(_FakeRequest(client_ip="198.51.100.2")) is None


def test_global_ceiling_stops_a_distributed_attempt(monkeypatch):
    """The load-bearing control: per-IP is bypassable, the global cap is not."""
    monkeypatch.setattr(app, "APP_ACCESS_TOKEN", "right-token")
    monkeypatch.setattr(app, "ANON_RATE_LIMIT", 1000)   # per-IP out of the way
    monkeypatch.setattr(app, "ANON_GLOBAL_LIMIT", 5)
    for i in range(5):
        assert app._enforce_access(_FakeRequest(client_ip=f"198.51.100.{i}")) is None
    with pytest.raises(HTTPException) as exc:
        app._enforce_access(_FakeRequest(client_ip="198.51.100.200"))
    assert exc.value.status_code == 429
    assert "shared hourly limit" in exc.value.detail


def test_a_privileged_caller_is_never_rate_limited(monkeypatch):
    monkeypatch.setattr(app, "APP_ACCESS_TOKEN", "right-token")
    monkeypatch.setattr(app, "ANON_RATE_LIMIT", 1)
    monkeypatch.setattr(app, "ANON_GLOBAL_LIMIT", 1)
    for _ in range(20):
        req = _FakeRequest({"x-app-token": "right-token"})
        assert app._enforce_access(req) is None
        assert req.state.privileged is True
    # A privileged caller must not consume the anonymous budget either.
    assert len(app._anon_global_hits) == 0


def test_a_refused_request_is_not_charged(monkeypatch):
    """Otherwise a blocked client pushes its own window out forever."""
    monkeypatch.setattr(app, "APP_ACCESS_TOKEN", "right-token")
    monkeypatch.setattr(app, "ANON_RATE_LIMIT", 2)
    for _ in range(2):
        app._enforce_access(_FakeRequest(client_ip="198.51.100.5"))
    before = len(app._anon_global_hits)
    for _ in range(5):
        with pytest.raises(HTTPException):
            app._enforce_access(_FakeRequest(client_ip="198.51.100.5"))
    assert len(app._anon_global_hits) == before


def test_stale_per_ip_windows_are_swept(monkeypatch):
    """A deque per IP that ever visited would be an unbounded memory leak.

    Pruning is lazy and per-IP, so an IP that never returns keeps a deque of
    expired timestamps. Deleting only already-empty deques does not help,
    because nothing ever empties them.
    """
    monkeypatch.setattr(app, "APP_ACCESS_TOKEN", "right-token")
    monkeypatch.setattr(app, "ANON_RATE_WINDOW_S", 0)  # every hit expires at once
    monkeypatch.setattr(app, "_SWEEP_EVERY", 10)
    monkeypatch.setattr(app, "_sweep_countdown", 10)
    for i in range(200):
        app._enforce_access(_FakeRequest(client_ip=f"198.51.100.{i % 250}"))
    assert len(app._anon_hits) <= 10, f"leaked {len(app._anon_hits)} per-IP windows"


def test_anon_gets_tighter_per_request_caps(monkeypatch):
    monkeypatch.setattr(app, "MAX_TTS_CHARS", 2000)
    monkeypatch.setattr(app, "ANON_MAX_TTS_CHARS", 400)
    monkeypatch.setattr(app, "MAX_UPLOAD_BYTES", 100)
    monkeypatch.setattr(app, "ANON_MAX_UPLOAD_BYTES", 10)

    priv, anon = _FakeRequest(), _FakeRequest()
    priv.state.privileged = True
    anon.state.privileged = False
    assert app._tts_char_cap(priv) == 2000
    assert app._tts_char_cap(anon) == 400
    assert app._upload_byte_cap(priv) == 100
    assert app._upload_byte_cap(anon) == 10
    # An unset state must fail CLOSED to the tighter cap, not the looser one.
    assert app._tts_char_cap(_FakeRequest()) == 400
    assert app._upload_byte_cap(_FakeRequest()) == 10


def test_ui_shell_is_public_but_key_spending_routes_are_metered():
    """The whole point of the design: browsable page, metered capability."""
    metered, open_routes = set(), set()
    for route in app.fastapi_app.routes:
        path = getattr(route, "path", None)
        if path is None:
            continue
        names = [d.dependency.__name__ for d in getattr(route, "dependencies", [])]
        (metered if "_enforce_access" in names else open_routes).add(path)

    assert "/" in open_routes, "the UI shell must stay public"
    for path in ("/upload", "/files/{filename}", "/transcribe",
                 "/api/tts-transcribe", "/api/tts-voices"):
        assert path in metered, f"{path} spends the Deepgram key and must be metered"


def test_socketio_connect_is_metered_too():
    """Mic streaming spends the key over the socket, so it is API surface."""
    src = (Path(__file__).resolve().parents[1] / "app.py").read_text()
    connect = src.split("async def connect(")[1].split("async def disconnect")[0]
    assert "_is_privileged" in connect, "connect does not resolve a tier"
    assert "ANON_MAX_CONCURRENT_STREAMS" in connect, "connect does not cap anon concurrency"
    assert "ConnectionRefusedError" in connect, "connect cannot refuse"


def test_anon_streams_have_a_wall_clock_cap():
    """A per-request rate limit cannot bound one socket streaming for hours."""
    src = (Path(__file__).resolve().parents[1] / "app.py").read_text()
    task = src.split("async def streaming_task(")[1].split("async def file_streaming_task")[0]
    assert "ANON_MAX_STREAM_SECONDS" in task, "no wall-clock cap on anonymous streams"
    assert "asyncio.wait_for" in task, "cap is not enforced with a timeout"


def test_token_comparison_is_constant_time():
    src = (Path(__file__).resolve().parents[1] / "app.py").read_text()
    body = src.split("def _is_privileged(")[1].split("\ndef ")[0]
    assert "compare_digest" in body, "use secrets.compare_digest, not =="


def test_socket_tier_map_is_cleaned_up_on_disconnect():
    """Otherwise the map leaks one entry per socket ever connected."""
    src = (Path(__file__).resolve().parents[1] / "app.py").read_text()
    disc = src.split("async def disconnect(")[1].split("@sio.on(")[0]
    assert "_socket_tiers.pop" in disc, "disconnect does not drop the tier entry"


def test_anon_cannot_hand_deepgram_an_unbounded_url():
    """Uploads are capped as they stream; a URL is an object of unknown length.

    The per-request rate limit cannot bound it, because one allowed request can
    point at a ten-hour recording, so /transcribe HEADs the URL for anonymous
    callers and refuses an unknown or oversized length.
    """
    src = (Path(__file__).resolve().parents[1] / "app.py").read_text()
    handler = src.split('@fastapi_app.post("/transcribe"')[1]
    assert "_check_remote_audio_size" in handler, "anon URL size is unchecked"
    assert "privileged" in handler, "the check is not tier-aware"


async def test_remote_audio_size_refuses_an_unknown_length(monkeypatch):
    """Failing open on a missing Content-Length would make the cap decorative."""
    class _Resp:
        status_code = 200
        headers: dict = {}

    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def head(self, url): return _Resp()

    monkeypatch.setattr(app.httpx, "AsyncClient", lambda **kw: _Client())
    reason = await app._check_remote_audio_size("https://x.test/a.wav", 1000)
    assert reason is not None and "did not report a size" in reason


async def test_remote_audio_size_refuses_an_oversized_file(monkeypatch):
    class _Resp:
        status_code = 200
        headers = {"content-length": "5000"}

    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def head(self, url): return _Resp()

    monkeypatch.setattr(app.httpx, "AsyncClient", lambda **kw: _Client())
    reason = await app._check_remote_audio_size("https://x.test/a.wav", 1000)
    assert reason is not None and "larger than" in reason


async def test_remote_audio_size_allows_a_small_file(monkeypatch):
    class _Resp:
        status_code = 200
        headers = {"content-length": "500"}

    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def head(self, url): return _Resp()

    monkeypatch.setattr(app.httpx, "AsyncClient", lambda **kw: _Client())
    assert await app._check_remote_audio_size("https://x.test/a.wav", 1000) is None
