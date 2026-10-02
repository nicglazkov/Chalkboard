import asyncio
import json
from unittest.mock import patch

import httpx
import pytest

from pipeline.tts import elevenlabs_tts


def _segments(n):
    return [{"text": f"Segment {i}.", "estimated_duration_sec": 1.0} for i in range(n)]


@pytest.fixture
def fake_api(monkeypatch):
    """Route the backend's httpx client to an in-process handler; record bodies."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        pcm = b"\x00\x00" * 24000            # exactly 1 second at 24 kHz, 16-bit mono
        return httpx.Response(200, content=pcm, headers={"request-id": f"req-{len(calls)}"})

    real_client = httpx.Client

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    with patch.object(elevenlabs_tts.httpx, "Client", side_effect=client_factory):
        yield calls


def test_writes_wav_with_measured_durations(tmp_path, fake_api):
    out = tmp_path / "voiceover.wav"
    path, durations = asyncio.run(elevenlabs_tts.generate_audio(_segments(2), out, model="eleven_v4"))
    assert path == out and out.exists()
    assert durations == [1.0, 1.0]


def test_v4_stitches_segments_in_order(tmp_path, fake_api):
    asyncio.run(elevenlabs_tts.generate_audio(_segments(3), tmp_path / "v.wav", model="eleven_v4"))
    assert "previous_request_ids" not in fake_api[0]
    assert fake_api[0]["next_text"] == "Segment 1."
    assert fake_api[1]["previous_request_ids"] == ["req-1"]
    assert fake_api[2]["previous_request_ids"] == ["req-1", "req-2"]
    assert fake_api[2]["previous_text"] == "Segment 1."


def test_v3_sends_no_context(tmp_path, fake_api):
    asyncio.run(elevenlabs_tts.generate_audio(_segments(3), tmp_path / "v.wav", model="eleven_v3"))
    for body in fake_api:
        assert not {"previous_text", "next_text", "previous_request_ids"} & set(body)


def test_missing_key_is_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ELEVENLABS_API_KEY"):
        asyncio.run(elevenlabs_tts.generate_audio(_segments(1), tmp_path / "v.wav"))
