import pytest
from stt.options import clean_params, Mode


def test_removes_internal_params():
    result = clean_params({"model": "nova-3", "base_url": "api.deepgram.com"}, Mode.STREAMING)
    assert "base_url" not in result
    assert result["model"] == "nova-3"


def test_removes_falsy_values():
    result = clean_params({"model": "nova-3", "version": "", "channels": 0, "encoding": None, "redact": []}, Mode.STREAMING)
    assert "version" not in result
    assert "channels" not in result
    assert "encoding" not in result
    assert "redact" not in result


def test_keeps_zero_when_meaningful():
    # 0 is falsy but some numeric params default to 0 — clean_params strips them
    result = clean_params({"alternatives": 0}, Mode.STREAMING)
    assert "alternatives" not in result


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
    params = {"redact": ["pci", "ssn"], "keyterms": ["hello:2", "world"]}
    result = clean_params(params, Mode.STREAMING)
    assert result["redact"] == ["pci", "ssn"]
    assert result["keyterms"] == ["hello:2", "world"]
