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

from app import _connect_stream, _params_to_sdk_kwargs


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
    """Connect the SDK to a local WS server; return the path it requested.

    Routes through _connect_stream, the same dispatcher the app uses, so the
    endpoint choice (v1 vs Flux's v2) is part of what these tests assert rather
    than something they assume.
    """
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
            async with _connect_stream(dg, _params_to_sdk_kwargs(params)):
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


# --- Flux / /v2/listen ---------------------------------------------------
#
# Everything below is measured behaviour, not documentation. /v2/listen accepts
# a small closed set of params and answers ANY deviation with the same opaque
# line — "400 Unexpected error when initializing websocket connection" — which
# names no parameter. So a regression that starts forwarding a v1 param to Flux
# does not produce a diagnosable failure in production; it produces a stream
# that will not start, for no stated reason. These tests are the early warning.


def _path_and_pairs(path: str) -> tuple[str, list[tuple[str, str]]]:
    from urllib.parse import urlsplit

    return urlsplit(path).path, _pairs(path)


@pytest.mark.asyncio
async def test_flux_model_goes_to_the_v2_endpoint():
    """A Flux model must change the ENDPOINT, not just the model param.

    On /v1/listen a flux model is a 400 (V2_MODEL_ON_V1_LISTEN_ENDPOINT), so
    getting this wrong breaks Flux completely rather than subtly.
    """
    endpoint, pairs = _path_and_pairs(await _handshake_path({"model": "flux-general-en"}))
    assert endpoint == "/v2/listen"
    assert ("model", "flux-general-en") in pairs


@pytest.mark.asyncio
async def test_non_flux_models_stay_on_v1():
    endpoint, _ = _path_and_pairs(await _handshake_path({"model": "nova-3"}))
    assert endpoint == "/v1/listen"


@pytest.mark.asyncio
async def test_flux_end_of_turn_params_reach_the_wire():
    """The three knobs that are the entire reason to choose Flux."""
    pairs = _pairs(await _handshake_path({
        "model": "flux-general-en",
        "eot_threshold": 0.8,
        "eager_eot_threshold": 0.4,
        "eot_timeout_ms": 7000,
    }))
    assert ("eot_threshold", "0.8") in pairs
    assert ("eager_eot_threshold", "0.4") in pairs
    assert ("eot_timeout_ms", "7000") in pairs


@pytest.mark.asyncio
async def test_flux_strips_every_v1_only_param():
    """The params panel always sends these. On Flux they must not reach the wire.

    Each one was verified to 400 the real handshake on its own, so a single leak
    here is a dead stream, not a degraded one.
    """
    pairs = _pairs(await _handshake_path({
        "model": "flux-general-en",
        "smart_format": True,
        "punctuate": True,
        "interim_results": True,
        "vad_events": True,
        "diarize": True,
        "diarize_model": "v2",
        "filler_words": True,
        "utterance_end_ms": 1000,
        "endpointing": 300,
        "no_delay": True,
        "multichannel": True,
        "detect_entities": True,
        "entity_prompt": "member ids",
        "alternatives": 3,
        "word_confidence": True,
        "version": "latest",
        "language": "en",
        "utterances": True,
        "paragraphs": True,
        "dictation": True,
        "search": "foo",
        "replace": "a:b",
    }))
    leaked = sorted(k for k, _ in pairs if k != "model")
    assert leaked == [], f"v1-only params reached /v2/listen: {leaked}"


@pytest.mark.asyncio
async def test_flux_keeps_the_params_v2_actually_accepts():
    pairs = _pairs(await _handshake_path({
        "model": "flux-general-multi",
        "encoding": "linear16",
        "sample_rate": 16000,
        "keyterms": ["perineorrhaphy", "cerebral palsy"],
        "numerals": True,
        "profanity_filter": True,
        "mip_opt_out": True,
        "tags": "flux-probe",
        "redact": ["numbers"],
        "language_hint": "es",
    }))
    for expected in (
        ("encoding", "linear16"),
        ("sample_rate", "16000"),
        ("keyterm", "perineorrhaphy"),
        ("keyterm", "cerebral palsy"),
        ("numerals", "true"),
        ("profanity_filter", "true"),
        ("mip_opt_out", "true"),
        ("tag", "flux-probe"),
        ("redact", "numbers"),
        ("language_hint", "es"),
    ):
        assert expected in pairs, f"{expected[0]} never reached /v2/listen: {pairs}"


@pytest.mark.asyncio
async def test_flux_still_refuses_the_denied_params():
    """The deny list is about safety, so the endpoint switch must not bypass it."""
    keys = [k for k, _ in _pairs(await _handshake_path({
        "model": "flux-general-en",
        "callback": "https://evil.example/collect",
        "keywords": "term:2",
        "base_url": "api.deepgram.com",
    }))]
    for forbidden in ("callback", "keywords", "base_url"):
        assert forbidden not in keys


def test_flux_messages_arrive_as_dicts_not_sdk_models():
    """Pins the SDK behaviour the Flux handler is built around.

    V2SocketClientResponse is a Union containing a bare `Any`, so construct_type
    matches that first and every /v2/listen message comes back as a plain dict.
    isinstance(msg, ListenV2TurnInfo) is therefore always False, and a handler
    written the way the v1 handler is written drops every turn in silence.

    If a future SDK tightens the union this test fails, which is the point: that
    is the moment to check the handler still works, not six months later when
    someone notices Flux has been mute.
    """
    import typing

    from deepgram.listen.v2.socket_client import V2SocketClientResponse

    assert typing.Any in typing.get_args(V2SocketClientResponse), (
        "the v2 response union no longer contains Any — messages may now arrive "
        "as models, so re-check _as_dict and _transcript_event"
    )


def test_v1_messages_still_arrive_as_models():
    """The other half of the asymmetry: v1's union has no `Any`.

    _transcript_event branches on isinstance for v1 and on the dict shape for
    Flux precisely because of this difference. If v1 ever grows an `Any` too,
    the v1 branch stops firing and the transcript pane goes blank.
    """
    import typing

    from deepgram.listen.v1.socket_client import V1SocketClientResponse

    assert typing.Any not in typing.get_args(V1SocketClientResponse)


def test_transcript_event_maps_a_flux_turn():
    import app as app_mod

    turn = {
        "type": "TurnInfo",
        "event": "Update",
        "turn_index": 2,
        "transcript": "Hi I need to cancel",
        "end_of_turn_confidence": 0.13,
        "audio_window_start": 2.56,
        "audio_window_end": 4.0,
        "words": [{"word": "Hi", "confidence": 1.0}],
    }
    payload = app_mod._transcript_event(turn)
    assert payload["transcript"] == "Hi I need to cancel"
    assert payload["is_final"] is False
    assert payload["turn_index"] == 2
    assert payload["end_of_turn_confidence"] == 0.13

    payload = app_mod._transcript_event({**turn, "event": "EndOfTurn"})
    assert payload["is_final"] is True


def test_only_end_of_turn_is_final():
    """EagerEndOfTurn carries the same transcript as the EndOfTurn that follows,
    but a TurnResumed can land in between and extend the turn. Treating it as
    final would commit a line the speaker had not finished."""
    import app as app_mod

    for event in ("Update", "StartOfTurn", "EagerEndOfTurn", "TurnResumed"):
        payload = app_mod._transcript_event(
            {"type": "TurnInfo", "event": event, "transcript": "hello"}
        )
        assert payload["is_final"] is False, event


def test_transcript_event_ignores_non_transcript_messages():
    import app as app_mod

    for msg in (
        {"type": "Connected", "request_id": "abc"},
        {"type": "FatalError", "description": "boom"},
        {"type": "Metadata"},
    ):
        assert app_mod._transcript_event(msg) is None


def test_clean_error_redacts_credentials_when_parsing_fails():
    """The leak this closed. _clean_error only stripped the Authorization header
    as a side effect of extracting `status_code: N, body: ...`; an SDK error
    without that tail fell through to `return str(e)` with the key intact, and
    /v2/listen handshake failures are exactly that shape."""
    import app as app_mod

    e = Exception(
        "ApiError: headers: {'Authorization': 'Token sk-live-secret'}: connection refused"
    )
    msg = app_mod._clean_error(e)
    assert "sk-live-secret" not in msg
    assert "<redacted>" in msg


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
