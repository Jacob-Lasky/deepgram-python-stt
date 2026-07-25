# tests/test_security.py
# Regression tests for the abuse surface of a PUBLIC, unauthenticated app.
# Every test here corresponds to something verified exploitable on 2026-07-25.
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
