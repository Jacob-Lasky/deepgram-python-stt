import urllib.parse
from enum import Enum


class Mode(str, Enum):
    STREAMING = "streaming"
    BATCH = "batch"
    BOTH = "both"


# --- Flux (/v2/listen) ---------------------------------------------------
#
# Flux is a DIFFERENT ENDPOINT, not another model on /v1/listen. Everything
# below exists because the two endpoints disagree about what a parameter is.
#
# /v1/listen is permissive: it drops query params it does not recognise and
# transcribes anyway, which is why so much of this file is about noticing
# silent no-ops. /v2/listen is the opposite — it is STRICT. One unrecognised
# param and the whole handshake fails:
#
#   400 Unexpected error when initializing websocket connection.
#
# and that string is the entire body. It does not name the parameter, the
# value, or the reason. Measured against production on 2026-07-25: every one of
# smart_format, punctuate, interim_results, diarize, filler_words,
# utterance_end_ms, endpointing, keywords, callback, entity_prompt, version and
# an invented `totally_made_up` killed the connection with that same message.
#
# So the gate for Flux is an ALLOWLIST, not the denylist used for v1. The params
# panel always sends a pile of v1 fields; forwarding any of them means the
# stream never starts, and the user is told nothing useful about why.
FLUX_MODEL_PREFIX = "flux-"


def is_flux_model(model) -> bool:
    """True for the Flux family, which must go to /v2/listen instead of /v1."""
    return isinstance(model, str) and model.startswith(FLUX_MODEL_PREFIX)


# Params /v2/listen accepts. Verified by handshaking each one against
# production rather than read off a page, because both available sources are
# wrong in a direction that matters:
#   - The docs' Flux feature-overview table lists `version` as "All available".
#     It is not: version=latest is a 400.
#   - The SDK's connect() names `language_hint` unconditionally, but it is a 400
#     on flux-general-en. See FLUX_MULTILINGUAL_ONLY below.
# DO NOT add to this set from either source alone. Handshake it first.
FLUX_PARAMS = {
    "model",
    "encoding",
    "sample_rate",
    "eot_threshold",
    "eager_eot_threshold",
    "eot_timeout_ms",
    "keyterm",
    "language_hint",
    "profanity_filter",
    "numerals",
    "redact",
    "mip_opt_out",
    "tag",
}

# Accepted by flux-general-multi ONLY. On flux-general-en these 400 the
# handshake.
#
# These are REPORTED by flux_validation_error, not stripped. The distinction
# matters: the params stripped above are panel defaults the user never chose,
# but a language hint is something they typed on purpose, and silently dropping
# it would produce a run that looks like it applied and did not — the exact
# failure this module exists to prevent.
FLUX_MULTILINGUAL_ONLY = {"language_hint"}
FLUX_MULTILINGUAL_MODEL = "flux-general-multi"

# Flux only redacts numbers. `redact=pci`, valid on v1, is a 400 here.
FLUX_REDACT_VALUES = {"numbers", "aggressive_numbers"}

# Inclusive bounds, measured by bisecting each one against production. The
# app validates against these BEFORE connecting, purely so the user gets a
# message naming the parameter — Deepgram's own rejection does not.
#
# DO NOT trust the docs here either: /docs/flux/configuration documents
# eot_timeout_ms as 500-10000, but 10001 through 60000 all connect fine and
# 60001 is the first rejection. The quickstart page has the correct range.
FLUX_RANGES = {
    "eot_threshold": (0.5, 0.9),
    "eager_eot_threshold": (0.3, 0.9),
    "eot_timeout_ms": (500, 60000),
}


# Mode gating, taken from Deepgram's own docs capability matrix. Each STT feature
# page carries machine-readable markers, e.g. filler-words.mdx has
#   <Markdown src="/snippets/stt-batch-available.mdx" />
#   <Markdown src="/snippets/stt-stream-unavailable.mdx" />
# so these two sets are transcribed from `stt-stream-unavailable` and
# `stt-batch-unavailable` respectively. DO NOT add a param here from memory:
# check the feature's page in deepgram-docs, because guessing wrong in either
# direction is invisible. Sending a batch-only param to streaming is SILENTLY
# IGNORED (the feature you think you are testing was never applied), and
# stripping a param that is actually valid is equally silent.
#
# Params that are ONLY valid in streaming mode (docs: stt-batch-unavailable)
STREAMING_ONLY = {
    "interim_results",
    "vad_events",
    "endpointing",
    "utterance_end_ms",
    "no_delay",
    "channels",
    "encoding",
    "sample_rate",
}

# Params that are ONLY valid in batch mode (docs: stt-stream-unavailable)
BATCH_ONLY = {
    "paragraphs",
    "topics",
    "intents",
    "sentiment",
    "utterances",
    "filler_words",
    "measurements",
    "utt_split",
    "detect_language",
}

# Numeric params where the UI's default of 0 means "unset", not "zero". Both of
# these fields render as 0 in the params panel, and Deepgram returns 400 for
# `sample_rate=0` or `channels=0`, so forwarding the default broke every batch
# request that touched them.
#
# DO NOT generalise this to "drop all zeroes". `endpointing=0` is meaningful: it
# disables endpointing. That is why this is an explicit set and not a rule.
ZERO_MEANS_UNSET = {"sample_rate", "channels", "alternatives"}

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
    flux = is_flux_model(params.get("model"))
    result: dict = {}
    for key, value in params.items():
        wire = PARAM_ALIASES.get(key, key)
        if _is_blocked(key, wire):
            continue
        if flux:
            # Allowlist, not mode gating: see FLUX_PARAMS. A Flux request is
            # bound for /v2/listen regardless of which of this app's modes the
            # user is in, and one stray v1 param there is a dead handshake.
            if wire not in FLUX_PARAMS:
                continue
        elif mode == Mode.STREAMING and wire in BATCH_ONLY:
            continue
        elif mode == Mode.BATCH and wire in STREAMING_ONLY:
            continue
        # Skip falsy values (but not 0 for numeric params, not False for booleans that are explicitly set)
        if value is None or value == "" or value == [] or value == {}:
            continue
        if isinstance(value, bool) and not value:
            continue
        if wire in ZERO_MEANS_UNSET and value in (0, "0"):
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
    #
    # It skips the Flux allowlist too, and that is deliberate. On Flux an
    # unknown param kills the handshake, so this is the one way to try a v2
    # param before this module models it — which is what a diagnostic tool is
    # for. The blast radius is a failed connect, and the user opted in.
    if "extra" in result and isinstance(result["extra"], dict):
        extra = result.pop("extra")
        for k, v in extra.items():
            wire = PARAM_ALIASES.get(k, k)
            if _is_blocked(k, wire):
                continue
            _put(result, wire, v)

    # `diarize_model` is mutually exclusive with the deprecated `diarize` and
    # `diarize_version`. Deepgram does not pick a winner, it rejects outright:
    #
    #   400 diarize_model cannot be used together with diarize or
    #       diarize_version. (INVALID_QUERY_PARAMETER)
    #
    # `diarize_model` wins here because it is the current parameter and the only
    # way to reach the v2 diarizer: the boolean `diarize` ALWAYS routes to v1,
    # and `diarize_version` is legacy. The UI already makes the two controls
    # exclusive; this covers the `extra` escape hatch and any saved config from
    # before the field was renamed, so an old preset degrades to the modern
    # parameter instead of a hard 400.
    if "diarize_model" in result:
        for legacy in ("diarize", "diarize_version"):
            result.pop(legacy, None)

    return result


def flux_validation_error(params: dict) -> str | None:
    """The reason /v2/listen is about to refuse this request, or None.

    THE POINT OF THIS FUNCTION IS THE MESSAGE. Deepgram validates all of these
    itself and rejects the handshake, but the entire body it returns is

        400 Unexpected error when initializing websocket connection.

    with no parameter name, no value, and no reason. In a tool whose only job is
    to explain what Deepgram did with your parameters, forwarding that is a
    non-answer. So the checks below are duplicated deliberately: not to protect
    Deepgram, but to say WHICH knob is wrong.

    DO NOT convert these into silent strips. A user who set eot_threshold=0.95
    needs to be told 0.9 is the ceiling; a run that quietly used 0.7 instead
    looks like evidence about a threshold that was never applied.

    Only checks Flux; returns None for every other model.
    """
    if not is_flux_model(params.get("model")):
        return None

    for name, (low, high) in FLUX_RANGES.items():
        raw = params.get(name)
        if raw is None or raw == "":
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return f"{name} must be a number between {low} and {high} (got {raw!r})."
        if not (low <= value <= high):
            return f"{name} must be between {low} and {high} (got {raw})."

    eager = params.get("eager_eot_threshold")
    eot = params.get("eot_threshold")
    if eager not in (None, "") and eot not in (None, ""):
        if float(eager) > float(eot):
            return (
                f"eager_eot_threshold ({eager}) must be less than or equal to "
                f"eot_threshold ({eot}). Eager end-of-turn fires before the real "
                "one, so a higher threshold could never trigger first."
            )

    redact = params.get("redact")
    if redact:
        values = redact if isinstance(redact, list) else [redact]
        bad = [v for v in values if v not in FLUX_REDACT_VALUES]
        if bad:
            return (
                f"Flux only redacts numbers. {', '.join(map(str, bad))} "
                f"{'is' if len(bad) == 1 else 'are'} not accepted; use "
                f"{' or '.join(sorted(FLUX_REDACT_VALUES))}."
            )

    if params.get("model") != FLUX_MULTILINGUAL_MODEL:
        for name in sorted(FLUX_MULTILINGUAL_ONLY):
            if params.get(name):
                return (
                    f"{name} requires model={FLUX_MULTILINGUAL_MODEL}. "
                    f"{params.get('model')} is English-only and rejects it."
                )

    return None


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
