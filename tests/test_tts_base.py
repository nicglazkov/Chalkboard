import pytest
from pipeline.tts.base import get_backend


def test_get_backend_returns_kokoro_by_default():
    backend = get_backend("kokoro")
    assert callable(backend)


def test_get_backend_raises_on_unknown():
    with pytest.raises(ValueError, match="Unknown TTS backend"):
        get_backend("unknown_backend")
