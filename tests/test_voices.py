import asyncio
from unittest.mock import patch

import pytest

from pipeline.tts import voices


def test_unset_narrator_means_backend_default():
    assert voices.resolve(None) is None
    assert voices.resolve("") is None


def test_unknown_narrator_is_rejected():
    with pytest.raises(ValueError):
        voices.resolve("nobody")


def test_every_narrator_names_a_real_backend():
    from pipeline.tts.base import get_backend
    for spec in voices.NARRATORS.values():
        assert spec["backend"] in ("kokoro", "elevenlabs", "openai")


def test_render_trigger_passes_narrator_voice(base_state, tmp_path):
    from pipeline.render_trigger import render_trigger
    seen = {}

    async def fake_generate(segments, output_path, speed=1.0, voice=None, model=None):
        seen.update(voice=voice, model=model)
        output_path.write_bytes(b"\x00")
        return output_path, [1.0] * len(segments)

    base_state.update(run_id="r1", manim_code="x", script="s", narrator="aria",
                      script_segments=[{"text": "Hi.", "estimated_duration_sec": 1.0}])
    with patch("pipeline.render_trigger.OUTPUT_DIR", str(tmp_path)), \
         patch("pipeline.render_trigger.get_backend", return_value=fake_generate) as gb:
        asyncio.run(render_trigger(base_state))
    gb.assert_called_once_with("elevenlabs")
    assert seen["voice"] == voices.NARRATORS["aria"]["voice"]
