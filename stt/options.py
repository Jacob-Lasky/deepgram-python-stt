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

# Params a caller must never be able to set, on any endpoint.
# `callback` makes Deepgram POST the finished transcript to a URL of the
# caller's choosing, so on a public unauthenticated app it is a data-exfil
# primitive AND a way to run arbitrary async jobs on the server's account.
# DO NOT move this into INTERNAL_PARAMS: those are stripped because the client
# consumes them, these are stripped because forwarding them is a vulnerability.
DENIED_PARAMS = {"callback"}


def clean_params(params: dict, mode: Mode) -> dict:
    """
    Remove internal params, mode-incompatible params, empty/falsy values,
    and handle special cases (keyterms list, redact list, etc.)
    Returns clean dict ready to send to Deepgram as query params.
    """
    result = {}
    for key, value in params.items():
        if key in INTERNAL_PARAMS or key in DENIED_PARAMS:
            continue
        if mode == Mode.STREAMING and key in BATCH_ONLY:
            continue
        if mode == Mode.BATCH and key in STREAMING_ONLY:
            continue
        # Skip falsy values (but not 0 for numeric params, not False for booleans that are explicitly set)
        if value is None or value == "" or value == [] or value == {}:
            continue
        if isinstance(value, bool) and not value:
            continue
        result[key] = value

    # Handle keyterms: list of "term" or "term:weight" strings -> repeated keyterm= params
    # (handled by requests library when value is a list)

    # Handle extra params: merge into result.
    # Re-apply the deny list here. This merge runs AFTER the filter loop above,
    # so without it a caller smuggles a blocked param straight through as
    # extra={"callback": "..."} and the deny list above does nothing.
    if "extra" in result and isinstance(result["extra"], dict):
        extra = result.pop("extra")
        result.update({
            k: v for k, v in extra.items()
            if k not in DENIED_PARAMS and k not in INTERNAL_PARAMS
        })

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
