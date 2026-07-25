"""What actually arrives at Deepgram's WebSocket, asserted against a real handshake.

Every other test in this suite checks the dict we hand to the SDK. That is the
producer half of the contract. This file checks the consumer half: it stands up a
real WebSocket server on loopback, points the SDK's environment at it, and reads
the request path the SDK actually sent.

Why this is not redundant with test_options.py: `_params_to_sdk_kwargs` splits
params into named kwargs and `request_options.additional_query_parameters`, and
nothing in a dict-shaped assertion proves the SDK does anything with the second
bucket. It could accept the kwarg and drop it. The keyterms bug was exactly this
class of failure — the value was present in our dict and absent on the wire — and
it survived because no test ever looked at a URL.

No Deepgram credential is needed, and none should ever be added here: the server
is ours, the handshake is local, and the assertion is on the path string.
"""
import inspect

import pytest
from deepgram import AsyncDeepgramClient
from deepgram.environment import DeepgramClientEnvironment
from websockets.asyncio.server import serve

from app import _params_to_sdk_kwargs


def _local_environment(port: int) -> DeepgramClientEnvironment:
    """Point every SDK endpoint at our loopback server.

    Built by introspecting the constructor rather than naming its fields, because
    the SDK adds URL fields between regenerations — 7.0 added `agent_rest`, which
    broke this helper with a TypeError while the app itself was fine. Enumerating
    the signature means the next added field costs nothing.
    """
    ws = f"ws://127.0.0.1:{port}"
    http = f"http://127.0.0.1:{port}"
    fields = inspect.signature(DeepgramClientEnvironment.__init__).parameters
    kwargs = {
        name: (http if "rest" in name or name == "base" else ws)
        for name in fields
        if name != "self"
    }
    return DeepgramClientEnvironment(**kwargs)


async def _handshake_path(params: dict) -> str:
    """Connect the SDK to a local WS server; return the path it requested."""
    seen: list[str] = []

    async def handler(ws):
        seen.append(ws.request.path)
        await ws.close()

    async with await serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        dg = AsyncDeepgramClient(
            api_key="wire-contract-test-key",
            environment=_local_environment(port),
        )
        try:
            async with dg.listen.v1.connect(**_params_to_sdk_kwargs(params)):
                pass
        except TypeError:
            # DO NOT swallow this. A TypeError from connect() IS the reported
            # bug ("got an unexpected keyword argument 'keyterms'"): it fires
            # before the handshake, so the assertion below would otherwise
            # report a confusing "server never saw a handshake" and hide the
            # symptom the user actually saw.
            raise
        except Exception:
            # Anything else is the server closing on us immediately, which is
            # what it is written to do. By this point the handshake, and
            # therefore the path, has already happened.
            pass

    assert seen, "server never saw a handshake"
    return seen[0]


def _pairs(path: str) -> list[tuple[str, str]]:
    from urllib.parse import parse_qsl, urlsplit

    return parse_qsl(urlsplit(path).query, keep_blank_values=True)


@pytest.mark.asyncio
async def test_sdk_named_params_reach_the_wire():
    pairs = _pairs(await _handshake_path({"model": "nova-3", "smart_format": True}))
    assert ("model", "nova-3") in pairs
    # Booleans must be the strings Deepgram accepts, never Python's "True".
    assert ("smart_format", "true") in pairs


@pytest.mark.asyncio
async def test_passthrough_params_reach_the_wire():
    """The six real Deepgram params the SDK does not enumerate as keywords.

    Before the kwarg split these raised TypeError at connect() and killed the
    stream. Asserting they are ON THE URL is the only proof that routing them
    through additional_query_parameters actually works.
    """
    params = {
        "model": "nova-3",
        "no_delay": True,
        "word_confidence": True,
        "alternatives": 3,
        "diarize_version": "v2",
        "entity_prompt": "patient names",
    }
    pairs = _pairs(await _handshake_path(params))
    for expected in (
        ("no_delay", "true"),
        ("word_confidence", "true"),
        ("alternatives", "3"),
        ("diarize_version", "v2"),
        ("entity_prompt", "patient names"),
    ):
        assert expected in pairs, f"{expected[0]} never reached the wire: {pairs}"

    # filler_words is the sixth of that group, but Deepgram's docs mark it
    # stream-unavailable, so on a STREAM it must be stripped rather than
    # forwarded and silently ignored. It reaches the wire in batch mode instead.
    assert "filler_words" not in [k for k, _ in pairs]


@pytest.mark.asyncio
async def test_keyterm_is_singular_and_repeated_per_term():
    """The reported bug, asserted at the wire.

    `keyterms` (plural) is a UI-internal name. Deepgram's param is `keyterm`,
    repeated once per term. A plural key on this URL means the alias regressed;
    a single comma-joined value means list encoding regressed.
    """
    pairs = _pairs(
        await _handshake_path(
            {"model": "nova-3", "keyterms": ["perineorrhaphy", "herniorrhaphy"]}
        )
    )
    assert ("keyterm", "perineorrhaphy") in pairs
    assert ("keyterm", "herniorrhaphy") in pairs
    assert not any(k == "keyterms" for k, _ in pairs), f"plural leaked: {pairs}"
    assert [v for k, v in pairs if k == "keyterm"] == [
        "perineorrhaphy",
        "herniorrhaphy",
    ]


@pytest.mark.asyncio
async def test_multiword_keyterm_stays_one_term():
    """A phrase must arrive as ONE keyterm value, not split into two params.
    Deepgram treats a single keyterm as one cohesive unit; splitting it changes
    what is being prompted for."""
    pairs = _pairs(
        await _handshake_path({"model": "nova-3", "keyterms": ["cerebral palsy"]})
    )
    assert [v for k, v in pairs if k == "keyterm"] == ["cerebral palsy"]


@pytest.mark.asyncio
async def test_denied_params_never_reach_the_wire():
    """`callback` is an exfil primitive and `keywords` is an unsupported
    Nova-2-only param. Neither may appear on the URL, including via `extra`."""
    params = {
        "model": "nova-3",
        "callback": "https://evil.example/collect",
        "keywords": "term:2",
        "extra": {"callback": "https://evil.example/collect2", "keywords": "x:3"},
    }
    keys = [k for k, _ in _pairs(await _handshake_path(params))]
    assert "callback" not in keys
    assert "keywords" not in keys


@pytest.mark.asyncio
async def test_base_url_is_never_forwarded_to_deepgram():
    """`base_url` selects the host client-side. Forwarding it as a query param
    would leak internal endpoint names into Deepgram's request logs."""
    path = await _handshake_path({"model": "nova-3", "base_url": "api.deepgram.com"})
    assert "base_url" not in [k for k, _ in _pairs(path)]


def test_clean_error_surfaces_deepgrams_reason_not_an_mdn_link():
    """A diagnostic tool must report WHY Deepgram refused the request.

    str(httpx.HTTPStatusError) is only "Client error '400 Bad Request' for url
    ... For more information check: <MDN link>", which names neither the
    parameter nor the cause. This handler used to return exactly that.
    """
    import httpx

    import app as app_mod

    request = httpx.Request("POST", "https://api.deepgram.com/v1/listen?alternatives=2")
    response = httpx.Response(
        400,
        json={"err_code": "Bad Request", "err_msg": "alternatives is not supported for this model"},
        request=request,
    )
    err = httpx.HTTPStatusError("boom", request=request, response=response)

    msg = app_mod._clean_error(err)
    assert "alternatives is not supported for this model" in msg
    assert "Deepgram 400" in msg
    assert "developer.mozilla.org" not in msg


def test_clean_error_handles_a_non_json_body():
    import httpx

    import app as app_mod

    request = httpx.Request("POST", "https://api.deepgram.com/v1/listen")
    response = httpx.Response(502, text="upstream unavailable", request=request)
    err = httpx.HTTPStatusError("boom", request=request, response=response)
    assert app_mod._clean_error(err) == "Deepgram 502: upstream unavailable"


def test_clean_error_explains_an_immediate_stream_close():
    """Deepgram rejects an unsupported param on a stream by closing with 1000 and
    no reason, which websockets reports as "received 1000 (OK)". That reads like a
    clean shutdown, so the message must say it is a rejection and how to get the
    real reason."""
    import websockets.exceptions
    import websockets.frames

    import app as app_mod

    # Reproduces the exact shape seen in production: close received, then sent,
    # both code 1000 with an empty reason. rcvd_then_sent must be non-None when
    # both frames are present (websockets asserts this).
    close = websockets.frames.Close(1000, "")
    err = websockets.exceptions.ConnectionClosedOK(close, close, True)
    msg = app_mod._clean_error(err)
    assert "closed the stream immediately" in msg
    assert "batch mode" in msg  # tells the user how to get the actual reason


def test_clean_error_still_strips_sdk_request_headers():
    """Load-bearing: SDK exception messages embed the full request including the
    Authorization header, so this must never degrade into returning str(e)."""
    import app as app_mod

    e = Exception(
        "ApiError: request: POST /v1/listen headers: {'Authorization': 'Token sk-secret'} "
        "status_code: 401, body: {'err_msg': 'Invalid credentials'}"
    )
    msg = app_mod._clean_error(e)
    assert "sk-secret" not in msg
    assert "Invalid credentials" in msg


def test_clean_error_does_not_repeat_the_error_code():
    """Deepgram usually prefixes err_msg with err_code, so appending it
    unconditionally produced "Bad Request: ... (Bad Request)". Observed live."""
    import httpx

    import app as app_mod

    request = httpx.Request("POST", "https://api.deepgram.com/v1/listen")
    response = httpx.Response(
        400,
        json={
            "err_code": "Bad Request",
            "err_msg": "Bad Request: Nova-3 models do not support more than one alternative.",
        },
        request=request,
    )
    msg = app_mod._clean_error(
        httpx.HTTPStatusError("boom", request=request, response=response)
    )
    assert msg == (
        "Deepgram 400: Bad Request: Nova-3 models do not support more than one alternative."
    )

    # A code that adds information is still appended.
    response = httpx.Response(
        400, json={"err_code": "INVALID_PARAM", "err_msg": "something went wrong"},
        request=request,
    )
    msg = app_mod._clean_error(
        httpx.HTTPStatusError("boom", request=request, response=response)
    )
    assert msg == "Deepgram 400: something went wrong (INVALID_PARAM)"
