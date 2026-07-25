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
