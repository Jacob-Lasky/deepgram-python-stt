import pytest

pytestmark = pytest.mark.skip(
    reason="Legacy SDK v5 test — not compatible with deepgram-sdk v6 or the current websocket-client approach"
)


def test_deepgram_live_transcription():
    pass
