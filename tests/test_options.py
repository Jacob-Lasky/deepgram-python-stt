import pytest
from stt.options import clean_params, Mode


def test_removes_internal_params():
    result = clean_params({"model": "nova-3", "base_url": "api.deepgram.com"}, Mode.STREAMING)
    assert "base_url" not in result
    assert result["model"] == "nova-3"


def test_removes_falsy_values():
    result = clean_params({"model": "nova-3", "version": "", "encoding": None, "redact": []}, Mode.STREAMING)
    assert "version" not in result
    assert "encoding" not in result
    assert "redact" not in result
    # 0 is NOT stripped — clean_params only strips None, "", [], {}
    assert result["model"] == "nova-3"


def test_keeps_zero_numerics():
    # clean_params does NOT strip 0 — callers are responsible for omitting defaults
    result = clean_params({"alternatives": 0, "channels": 0}, Mode.STREAMING)
    assert result["alternatives"] == 0
    assert result["channels"] == 0


def test_removes_false_booleans():
    result = clean_params({"smart_format": False, "diarize": False}, Mode.STREAMING)
    assert "smart_format" not in result
    assert "diarize" not in result


def test_keeps_true_booleans():
    result = clean_params({"smart_format": True, "diarize": True}, Mode.STREAMING)
    assert result["smart_format"] is True
    assert result["diarize"] is True


def test_streaming_strips_batch_only_params():
    params = {"model": "nova-3", "paragraphs": True, "topics": True, "intents": True, "sentiment": True, "utterances": True}
    result = clean_params(params, Mode.STREAMING)
    for key in ("paragraphs", "topics", "intents", "sentiment", "utterances"):
        assert key not in result
    assert result["model"] == "nova-3"


def test_batch_strips_streaming_only_params():
    params = {"model": "nova-3", "interim_results": True, "vad_events": True, "endpointing": 10, "utterance_end_ms": 1000, "no_delay": True}
    result = clean_params(params, Mode.BATCH)
    for key in ("interim_results", "vad_events", "endpointing", "utterance_end_ms", "no_delay"):
        assert key not in result
    assert result["model"] == "nova-3"


def test_extra_dict_merged():
    params = {"model": "nova-3", "extra": {"custom_key": "custom_val"}}
    result = clean_params(params, Mode.STREAMING)
    assert "extra" not in result
    assert result["custom_key"] == "custom_val"


def test_list_values_kept():
    # Bare terms only. "hello:2" would be a keyWORDS intensifier, which is a
    # different (Nova-2-only) parameter; as a keyterm it prompts for the literal
    # string "hello:2".
    params = {"redact": ["pci", "ssn"], "keyterms": ["hello", "world"]}
    result = clean_params(params, Mode.STREAMING)
    assert result["redact"] == ["pci", "ssn"]
    # keyterms is aliased to Deepgram's singular wire name; see PARAM_ALIASES.
    assert result["keyterm"] == ["hello", "world"]


# ---------------------------------------------------------------------------
# keyterms -> keyterm. This was a live bug that failed two different ways:
# streaming raised "unexpected keyword argument 'keyterms'", and batch silently
# ignored it so a keyterm experiment ran with no keyterms applied at all.
# ---------------------------------------------------------------------------

def test_keyterms_is_aliased_to_the_wire_name():
    from stt.options import clean_params, Mode
    out = clean_params({"model": "nova-3", "keyterms": ["alpha", "beta"]}, Mode.STREAMING)
    assert "keyterms" not in out, "the UI-internal plural must never reach Deepgram"
    assert out["keyterm"] == ["alpha", "beta"]


def test_keyterms_alias_applies_in_batch_mode_too():
    """Batch is where the wrong name was silently dropped rather than erroring."""
    from stt.options import clean_params, Mode
    out = clean_params({"keyterms": ["alpha"]}, Mode.BATCH)
    assert out == {"keyterm": ["alpha"]}


def test_keyterms_alias_applies_through_extra():
    from stt.options import clean_params, Mode
    out = clean_params({"extra": {"keyterms": ["alpha"]}}, Mode.BATCH)
    assert out == {"keyterm": ["alpha"]}


def test_keyterm_passed_directly_is_untouched():
    from stt.options import clean_params, Mode
    out = clean_params({"keyterm": ["alpha"]}, Mode.STREAMING)
    assert out == {"keyterm": ["alpha"]}


def test_query_string_repeats_keyterm_per_item():
    from stt.options import query_string, Mode
    qs = query_string({"keyterms": ["one", "two words"]}, Mode.STREAMING)
    assert qs.count("keyterm=") == 2
    assert "keyterms=" not in qs


def test_sdk_accepts_every_serialized_param_name():
    """Guards the whole class of bug: a param name the SDK rejects raises
    TypeError at connect() and kills the stream. Assert every name we emit for
    streaming is a real connect() keyword."""
    import inspect
    from deepgram import AsyncDeepgramClient
    from stt.options import serialize_params, Mode

    accepted = set(inspect.signature(
        AsyncDeepgramClient(api_key="x").listen.v1.connect
    ).parameters)

    import json
    from pathlib import Path
    defaults = json.loads(
        (Path(__file__).resolve().parents[1] / "config" / "defaults.json").read_text()
    )
    # Give every default a truthy value so none are dropped as falsy.
    probe = {}
    for k, v in defaults.items():
        if isinstance(v, bool):
            probe[k] = True
        elif isinstance(v, list):
            probe[k] = ["x"]
        elif isinstance(v, dict):
            continue          # `extra` is a passthrough bag, not a param itself
        elif isinstance(v, (int, float)):
            probe[k] = v or 1
        else:
            probe[k] = "x"

    emitted = set(serialize_params(probe, Mode.STREAMING))

    # Every emitted param must reach the wire by one of the two routes, and the
    # ones the SDK does not name must go through the passthrough rather than
    # being dropped or raising TypeError at connect().
    import app
    built = app._params_to_sdk_kwargs(probe)
    via_kwargs = set(built) - {"request_options"}
    via_query = set(
        built.get("request_options", {}).get("additional_query_parameters", {})
    )

    assert via_kwargs <= accepted, f"would raise TypeError at connect(): {sorted(via_kwargs - accepted)}"
    dropped = emitted - via_kwargs - via_query
    assert not dropped, f"params silently dropped, never reaching Deepgram: {sorted(dropped)}"
    # These are real Deepgram params the SDK does not enumerate; they must be
    # routed, not lost. Regression guard for the keyterms crash class.
    for name in ("filler_words", "no_delay", "word_confidence"):
        assert name in via_query, f"{name} is not being forwarded"


def test_nova2_keywords_is_not_supported():
    """Deliberately dropped. `keywords` is Nova-2-and-older keyword BOOSTING
    (`keywords=TERM:INTENSIFIER`); this app targets Nova-3 and Flux, where the
    equivalent is Keyterm Prompting via `keyterm`. Leaving a Nova-2-only param
    in the UI invites sending it on a Nova-3 request, where Deepgram silently
    ignores it and the user concludes the feature is broken."""
    from stt.options import clean_params, Mode
    for mode in (Mode.BATCH, Mode.STREAMING):
        assert "keywords" not in clean_params({"keywords": "term:2"}, mode)
        assert "keywords" not in clean_params({"extra": {"keywords": "term:2"}}, mode)


def test_keyterm_takes_bare_terms_not_intensifiers():
    """Keyterm Prompting has NO intensifier syntax. Documented so nobody
    reintroduces `term:2` thinking it weights the term; it would prompt for the
    literal string. Weights belong to `keywords`, which is unsupported here."""
    from stt.options import query_string, Mode
    qs = query_string({"keyterms": ["perineorrhaphy"]}, Mode.STREAMING)
    assert qs == "keyterm=perineorrhaphy"


def test_keywords_gone_from_ui_defaults():
    import json
    from pathlib import Path
    d = json.loads((Path(__file__).resolve().parents[1] / "config" / "defaults.json").read_text())
    assert "keywords" not in d


def test_alias_and_wire_name_from_two_sources_are_unioned_not_dropped():
    """A caller can reach one wire param by two names at once: the UI's plural
    `keyterms` and an explicit `extra={"keyterm": ...}`. A plain assignment
    dropped one of them depending on dict order, which is invisible in a
    transcript. Repeatable params union instead."""
    from stt.options import clean_params, Mode
    out = clean_params(
        {"keyterms": ["alpha"], "extra": {"keyterm": ["beta"]}}, Mode.STREAMING
    )
    assert out["keyterm"] == ["alpha", "beta"]

    # Order preserved, duplicates collapsed — a repeated keyterm is wire noise.
    out = clean_params(
        {"keyterms": ["alpha", "beta"], "extra": {"keyterm": "alpha"}}, Mode.STREAMING
    )
    assert out["keyterm"] == ["alpha", "beta"]


def test_non_repeatable_collision_still_takes_one_value():
    """Only params Deepgram accepts more than once are unioned. `model` is
    single-valued: unioning it would put a list where a string belongs."""
    from stt.options import clean_params, Mode
    out = clean_params({"model": "nova-3", "extra": {"model": "nova-2"}}, Mode.STREAMING)
    assert out["model"] == "nova-2"  # extra is the escape hatch and wins


def test_alias_cannot_smuggle_a_denied_wire_name():
    """The deny list is enforced on the name that actually goes on the wire, not
    just the name the caller typed, so a future alias pointing at a denied param
    cannot bypass it. Guards the invariant, not today's alias table."""
    from stt.options import clean_params, Mode, PARAM_ALIASES, DENIED_PARAMS
    import stt.options as options

    original = dict(PARAM_ALIASES)
    try:
        options.PARAM_ALIASES = {**original, "harmless": "callback"}
        out = clean_params({"harmless": "https://evil.example/x"}, Mode.BATCH)
        assert out == {}, f"denied wire name reached the request: {out}"
        out = clean_params({"extra": {"harmless": "https://evil.example/x"}}, Mode.BATCH)
        assert out == {}, f"denied wire name reached the request via extra: {out}"
    finally:
        options.PARAM_ALIASES = original

    assert "callback" in DENIED_PARAMS
