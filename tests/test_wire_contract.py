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
import pytest
from deepgram import AsyncDeepgramClient
from deepgram.environment import DeepgramClientEnvironment
from websockets.asyncio.server import serve

from app import _params_to_sdk_kwargs


async def _handshake_path(params: dict) -> str:
    """Connect the SDK to a local WS server; return the path it requested."""
    seen: list[str] = []

    async def handler(ws):
        seen.append(ws.request.path)
        await ws.close()

    async with await serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        url = f"ws://127.0.0.1:{port}"
        dg = AsyncDeepgramClient(
            api_key="wire-contract-test-key",
            environment=DeepgramClientEnvironment(
                base=f"http://127.0.0.1:{port}", production=url, agent=url
            ),
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
        "filler_words": True,
        "no_delay": True,
        "word_confidence": True,
        "alternatives": 3,
        "diarize_version": "v2",
        "entity_prompt": "patient names",
    }
    pairs = _pairs(await _handshake_path(params))
    for expected in (
        ("filler_words", "true"),
        ("no_delay", "true"),
        ("word_confidence", "true"),
        ("alternatives", "3"),
        ("diarize_version", "v2"),
        ("entity_prompt", "patient names"),
    ):
        assert expected in pairs, f"{expected[0]} never reached the wire: {pairs}"


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
