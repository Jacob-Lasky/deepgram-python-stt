import urllib.parse
from enum import Enum


class Mode(str, Enum):
    STREAMING = "streaming"
    BATCH = "batch"
    BOTH = "both"


# Params that are ONLY valid in streaming mode
STREAMING_ONLY = {"interim_results", "vad_events", "endpointing", "utterance_end_ms", "no_delay"}

# Params that are ONLY valid in batch mode
BATCH_ONLY = {"paragraphs", "topics", "intents", "sentiment", "utterances"}

# Params that should never be sent to Deepgram (handled by client)
INTERNAL_PARAMS = {"base_url"}

# UI-internal name -> Deepgram wire name.
#
# The frontend models these as plural lists (`keyterms`, `tags`) but Deepgram's
# parameters are singular and repeated once per value (`keyterm`, `tag`).
# Translating here, at the single parameter gate, makes every call site correct
# at once.
#
# `keyterm` is KEYTERM PROMPTING (Nova-3 and Flux). It improves Keyword Recall
# Rate and takes BARE TERMS ONLY. It is NOT keyword boosting and has NO
# intensifier syntax: a value of "term:2" prompts for the literal string
# "term:2". Weighted boosting is the separate, legacy `keywords` parameter
# (Nova-2 and older), which this app deliberately does not support.
# Join words with %20 or + to prompt a multi-word phrase as one cohesive unit.
#
# This bug was live and it failed in two different ways, which is why the alias
# belongs in code and not in a comment telling people to remember:
#   - Streaming raised `AsyncV1Client.connect() got an unexpected keyword
#     argument 'keyterms'`, because the SDK only accepts `keyterm`.
#   - Batch SILENTLY IGNORED it. Deepgram drops unknown query params without
#     erroring, so a "with keyterms" experiment ran with no keyterms applied at
#     all and looked like evidence that keyterm prompting does not help.
# DO NOT rename the UI field to work around this; the alias is the fix.
PARAM_ALIASES = {"keyterms": "keyterm", "tags": "tag"}

# Wire params Deepgram accepts more than once per request (the key is repeated,
# once per value). Two sources can land on the SAME wire name: the UI's plural
# alias (`keyterms`) and a caller-supplied `extra={"keyterm": ...}`. A plain
# assignment drops one of them, and WHICH one depends on dict iteration order,
# so these are unioned instead. Deepgram repeats the key per value, so a union
# is exactly what the caller asked for. DO NOT collapse this back to an
# overwrite: silently dropping half a keyterm list is the same class of bug as
# the plural/singular mismatch above, and just as invisible in a transcript.
REPEATABLE_PARAMS = {"keyterm", "tag", "redact", "search", "replace"}

# Params a caller must never be able to set, on any endpoint.
# `callback` makes Deepgram POST the finished transcript to a URL of the
# caller's choosing, so on a public unauthenticated app it is a data-exfil
# primitive AND a way to run arbitrary async jobs on the server's account.
# DO NOT move this into INTERNAL_PARAMS: those are stripped because the client
# consumes them, these are stripped because forwarding them is a vulnerability.
DENIED_PARAMS = {
    "callback",
    # Legacy Nova-2-and-older keyword BOOSTING (`keywords=TERM:INTENSIFIER`).
    # Deliberately unsupported: this app targets Nova-3 and Flux, where the
    # equivalent is Keyterm Prompting via `keyterm`. Keeping a Nova-2-only
    # parameter in the UI invites sending it on a Nova-3 request, where Deepgram
    # silently ignores it and the user concludes the feature does not work.
    "keywords",
}


def _is_blocked(caller_name: str, wire_name: str) -> bool:
    """True if a param must not reach Deepgram.

    Tests BOTH the name the caller used and the wire name it maps to. Checking
    only the caller's name would let any future alias whose target is denied
    smuggle that target past the gate, because the deny list is enforced on the
    name that actually goes on the wire.
    """
    return bool({caller_name, wire_name} & (INTERNAL_PARAMS | DENIED_PARAMS))


def _put(result: dict, wire_name: str, value) -> None:
    """Write one already-aliased param, unioning repeatable ones (see REPEATABLE_PARAMS)."""
    if wire_name in result and wire_name in REPEATABLE_PARAMS:
        existing = result[wire_name] if isinstance(result[wire_name], list) else [result[wire_name]]
        incoming = value if isinstance(value, list) else [value]
        # dict.fromkeys dedupes while preserving order; a repeated keyterm is
        # noise on the wire, and order is what the user typed.
        result[wire_name] = list(dict.fromkeys([*existing, *incoming]))
        return
    result[wire_name] = value


def clean_params(params: dict, mode: Mode) -> dict:
    """
    Remove internal params, mode-incompatible params, empty/falsy values,
    and map UI-internal names to Deepgram wire names (see PARAM_ALIASES).
    Returns clean dict ready to send to Deepgram as query params.
    """
    result: dict = {}
    for key, value in params.items():
        wire = PARAM_ALIASES.get(key, key)
        if _is_blocked(key, wire):
            continue
        if mode == Mode.STREAMING and wire in BATCH_ONLY:
            continue
        if mode == Mode.BATCH and wire in STREAMING_ONLY:
            continue
        # Skip falsy values (but not 0 for numeric params, not False for booleans that are explicitly set)
        if value is None or value == "" or value == [] or value == {}:
            continue
        if isinstance(value, bool) and not value:
            continue
        _put(result, wire, value)

    # Handle extra params: merge into result. `extra` is the deliberate escape
    # hatch for params this app does not model, so it skips the mode and falsy
    # filters above — but NOT the deny list.
    #
    # Re-applying the deny list here is load-bearing. This merge runs AFTER the
    # filter loop, so without it a caller smuggles a blocked param straight
    # through as extra={"callback": "..."} and the deny list above does nothing.
    #
    # NOTE: Deepgram also has its own unrelated `extra` query param (arbitrary
    # metadata echoed back in the response). A non-dict `extra` is left alone
    # above and forwarded as that param; only a dict means "merge these".
    if "extra" in result and isinstance(result["extra"], dict):
        extra = result.pop("extra")
        for k, v in extra.items():
            wire = PARAM_ALIASES.get(k, k)
            if _is_blocked(k, wire):
                continue
            _put(result, wire, v)

    return result


def serialize_params(params: dict, mode: Mode) -> dict:
    """clean_params() plus Deepgram's wire encoding for each value.

    THE single place parameter values get encoded for Deepgram. There used to be
    five near-identical copies of this loop (three in app.py, one in
    _stt_streaming_raw, one in STTClient.build_url), so a change to how one
    value type is encoded had to be made in five places or silently diverge.

    Deepgram REJECTS Python bools, so they go as the lowercase strings
    "true"/"false". Lists stay lists: the HTTP layer repeats the key per item,
    which is how keyterm/redact take multiple values.
    """
    out: dict = {}
    for key, value in clean_params(params, mode).items():
        if isinstance(value, bool):
            out[key] = "true" if value else "false"
        elif isinstance(value, (list, str)):
            out[key] = value
        else:
            out[key] = str(value)
    return out


def query_string(params: dict, mode: Mode) -> str:
    """URL-encoded query string, repeating a key once per list item."""
    parts = []
    for key, value in serialize_params(params, mode).items():
        for item in (value if isinstance(value, list) else [value]):
            parts.append(f"{key}={urllib.parse.quote(str(item))}")
    return "&".join(parts)
