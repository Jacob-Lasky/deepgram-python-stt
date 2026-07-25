# tests/test_security.py
# Regression tests for this app's abuse surface. It holds a Deepgram key and
# calls Deepgram on a caller's behalf, so every request field is hostile input.
# The UI shell is public by design; the API and the SocketIO stream are gated.
# Each case here corresponds to something verified exploitable on 2026-07-25.
# Uses pytest-asyncio auto mode (no @pytest.mark.asyncio needed).
import os
from pathlib import Path

import pytest

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


def test_token_gate_is_off_when_no_token_is_configured():
    """Local dev and this suite run open; the gate is opt-in via the env var."""
    assert app.APP_ACCESS_TOKEN == ""
    assert app._require_api_token(_FakeRequest()) is None


class _FakeRequest:
    """Minimal stand-in for starlette Request: headers + query_params only."""

    def __init__(self, headers=None, query=None):
        self.headers = headers or {}
        self.query_params = query or {}


@pytest.mark.parametrize("headers,query", [
    ({}, {}),                                        # nothing supplied
    ({"x-app-token": "wrong"}, {}),                  # wrong header
    ({"authorization": "Bearer wrong"}, {}),         # wrong bearer
    ({}, {"token": "wrong"}),                        # wrong query param
    ({"x-app-token": ""}, {"token": "wrong"}),       # empty header, wrong query
])
def test_token_gate_rejects_bad_credentials(monkeypatch, headers, query):
    from fastapi import HTTPException
    monkeypatch.setattr(app, "APP_ACCESS_TOKEN", "right-token")
    with pytest.raises(HTTPException) as exc:
        app._require_api_token(_FakeRequest(headers, query))
    assert exc.value.status_code == 401


@pytest.mark.parametrize("headers,query", [
    ({"x-app-token": "right-token"}, {}),
    ({"authorization": "Bearer right-token"}, {}),
    ({"authorization": "bearer right-token"}, {}),   # scheme is case-insensitive
    ({}, {"token": "right-token"}),                  # <audio src> has no headers
])
def test_token_gate_accepts_good_credentials(monkeypatch, headers, query):
    monkeypatch.setattr(app, "APP_ACCESS_TOKEN", "right-token")
    assert app._require_api_token(_FakeRequest(headers, query)) is None


def test_ui_shell_is_public_but_key_spending_routes_are_gated():
    """The whole point of the design: browsable page, gated capability."""
    gated, open_routes = set(), set()
    for route in app.fastapi_app.routes:
        path = getattr(route, "path", None)
        if path is None:
            continue
        names = [d.dependency.__name__ for d in getattr(route, "dependencies", [])]
        (gated if "_require_api_token" in names else open_routes).add(path)

    assert "/" in open_routes, "the UI shell must stay public"
    for path in ("/upload", "/files/{filename}", "/transcribe",
                 "/api/tts-transcribe", "/api/tts-voices"):
        assert path in gated, f"{path} spends the Deepgram key and must be gated"


def test_socketio_connect_is_gated_too():
    """Mic streaming spends the key over the socket, so an open socket is a hole."""
    src = (Path(__file__).resolve().parents[1] / "app.py").read_text()
    connect = src.split("async def connect(")[1].split("async def disconnect")[0]
    assert "APP_ACCESS_TOKEN" in connect, "connect handler does not check the token"
    assert "ConnectionRefusedError" in connect, "connect handler does not refuse"
    assert "compare_digest" in connect, "connect handler must not use == on a secret"


def test_token_comparison_is_constant_time():
    src = (Path(__file__).resolve().parents[1] / "app.py").read_text()
    body = src.split("def _require_api_token(")[1].split("\nclass ")[0]
    assert "compare_digest" in body, "use secrets.compare_digest, not =="
