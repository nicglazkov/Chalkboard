# tests/test_pacing.py
import asyncio
import json

import numpy as np
import pytest
import soundfile as sf

from pipeline import pacing
from pipeline.cues import parse_cues, parse_markers, strip_cues

SR = 24000


def _tone(sec, amp=0.3):
    t = np.arange(int(round(sec * SR))) / SR
    return (amp * np.sin(2 * np.pi * 220 * t)).astype("float32")


def _sil(sec):
    return np.zeros(int(round(sec * SR)), dtype="float32")


# ── markers ────────────────────────────────────────────────────────────────

def test_beat_markers_are_stripped_and_located():
    clean, cues, beats = parse_markers("So what is it? [[beat]] It is [[1]] a line.")
    assert clean == "So what is it? It is a line."
    assert cues == {1: clean.index("a line")}
    assert beats == [clean.index("It is")]
    assert strip_cues("Wait [[pause]] here [[BEAT]].") == "Wait here."


def test_beat_glued_to_punctuation_moves_to_next_word():
    clean, _, beats = parse_markers("The answer[[beat]]. Next idea.")
    assert clean == "The answer. Next idea."
    assert beats == [clean.index("Next")]


def test_beat_at_segment_end_and_consecutive_beats():
    clean, _, beats = parse_markers("Done. [[beat]] [[beat]] Then more. [[beat]]")
    assert clean == "Done. Then more."
    assert beats == [clean.index("Then"), len(clean)]   # consecutive markers merge
    points, end_beat = pacing.pause_points(clean, beats)
    assert end_beat is True
    assert points == [(clean.index("Then"), "beat")]   # beat outranks the sentence end


def test_parse_cues_unchanged_by_beats():
    assert parse_cues("Start [[1]] here. [[beat]] Then [[2]] there.") == \
        ("Start here. Then there.", {1: 6, 2: 17})


# ── pause points ───────────────────────────────────────────────────────────

def test_pause_point_kinds():
    text = "Why is it so? Look at this: a line. Then e.g. something. Done!"
    points, end_beat = pacing.pause_points(text, [])
    kinds = {text[o:o + 4]: k for o, k in points}
    assert kinds == {"Look": "question", "a li": "payoff", "Then": "sentence", "Done": "sentence"}
    assert end_beat is False   # "e.g. something" is not a sentence end, the final "!" is the hold


def test_paragraph_break_is_a_beat():
    points, _ = pacing.pause_points("First idea.\n\nSecond idea.", [])
    assert points == [(13, "beat")]


def test_timing_text_round_trips():
    clean, cues, _ = parse_markers("A [[1]] cat sat. Then [[2]] a dog? Yes.")
    extra = [o for o, _ in pacing.pause_points(clean, [])[0]]
    marked = pacing.timing_text(clean, cues, extra, 3)
    c2, offs = parse_cues(marked)
    assert c2 == clean
    assert offs[1] == cues[1] and offs[2] == cues[2]
    assert [offs[3], offs[4]] == extra


# ── presets ────────────────────────────────────────────────────────────────

def test_default_preset_is_relaxed(monkeypatch):
    for k in ("PACE", "SCENE_HOLD_S", "PACE_SPEECH_SPEED"):
        monkeypatch.delenv(k, raising=False)
    p = pacing.resolve_pace()
    assert p.name == "relaxed" and p.hold >= 2.0 and p.speech_speed < 1.0


def test_preset_resolution_and_overrides(monkeypatch):
    monkeypatch.setenv("PACE", "brisk")
    assert pacing.resolve_pace().name == "brisk"
    assert pacing.resolve_pace("normal").name == "normal"   # explicit beats env
    monkeypatch.setenv("SCENE_HOLD_S", "3")
    monkeypatch.setenv("PACE_SPEECH_SPEED", "0.9")
    p = pacing.resolve_pace("relaxed")
    assert p.hold == 3.0 and p.speech_speed == 0.9 and p.sentence == pacing.PRESETS["relaxed"].sentence
    with pytest.raises(ValueError):
        pacing.resolve_pace("slow")
    monkeypatch.setenv("SCENE_HOLD_S", "x")
    with pytest.raises(ValueError):
        pacing.resolve_pace()


def test_presets_are_ordered():
    r, n, b = (pacing.PRESETS[k] for k in ("relaxed", "normal", "brisk"))
    for f in ("hold", "lead_in", "sentence", "question", "payoff", "beat"):
        assert getattr(r, f) > getattr(n, f) > getattr(b, f)
    assert r.speech_speed < n.speech_speed < b.speech_speed


def test_effective_speed_multiplies():
    assert pacing.effective_speed(pacing.PRESETS["relaxed"], 1.0) == 0.94
    assert pacing.effective_speed(pacing.PRESETS["normal"], 1.2) == 1.2


# ── silence insertion ──────────────────────────────────────────────────────

RELAXED = pacing.PRESETS["relaxed"]


def test_pace_segment_tops_up_lead_pause_and_hold():
    audio = np.concatenate([_sil(0.1), _tone(1.0), _sil(0.15), _tone(1.0), _sil(0.1)])
    r = pacing.pace_segment(audio, SR, RELAXED, [(15, "sentence", 1.25)])
    assert r["lead_pad"] == pytest.approx(0.4, abs=0.011)
    (off, pad, gap, kind, _), = r["pads"]
    assert (off, kind) == (15, "sentence")
    assert gap == pytest.approx(RELAXED.sentence, abs=0.011)
    assert pad == pytest.approx(0.5, abs=0.011)
    total = len(r["audio"]) / SR
    assert total == pytest.approx(2.35 + 0.4 + 0.5 + 1.9, abs=0.03)
    assert total - r["speech_end"] == pytest.approx(RELAXED.hold, abs=0.02)


def test_pace_segment_never_shortens_long_natural_silence():
    audio = np.concatenate([_sil(1.0), _tone(1.0), _sil(1.0), _tone(0.5), _sil(3.0)])
    r = pacing.pace_segment(audio, SR, RELAXED, [(10, "sentence", 2.0)])
    assert r["lead_pad"] == 0.0 and r["pads"] == []
    assert len(r["audio"]) == len(audio)


def test_pace_segment_end_beat_extends_hold():
    pace = pacing.Pace("t", hold=1.0, lead_in=0, sentence=0, question=0, payoff=0, beat=1.5, speech_speed=1)
    audio = np.concatenate([_tone(1.0), _sil(0.1)])
    r = pacing.pace_segment(audio, SR, pace, [], end_beat=True)
    assert len(r["audio"]) / SR - r["speech_end"] == pytest.approx(1.5, abs=0.02)


def test_pace_segment_skips_points_without_time_and_silent_audio():
    audio = np.concatenate([_tone(1.0), _sil(0.1), _tone(1.0)])
    r = pacing.pace_segment(audio, SR, RELAXED, [(5, "question", None)])
    assert r["pads"] == []
    silent = pacing.pace_segment(_sil(0.5), SR, RELAXED, [(1, "beat", 0.2)])
    assert len(silent["audio"]) / SR >= RELAXED.hold


def test_pace_segment_without_natural_gap_inserts_full_pause():
    audio = _tone(2.0)
    r = pacing.pace_segment(audio, SR, RELAXED, [(8, "beat", 1.0)])
    (_, pad, gap, _, at), = r["pads"]
    assert pad == pytest.approx(RELAXED.beat, abs=0.011) and gap == pytest.approx(pad)
    assert 0.6 <= at <= 1.1


def test_estimated_times_snap_to_the_nearest_natural_gap():
    audio = np.concatenate([_tone(1.0), _sil(0.2), _tone(1.0)])
    # OpenAI-style proportional estimate 0.6 s off: still lands in the real gap.
    r = pacing.pace_segment(audio, SR, RELAXED, [(10, "sentence", 1.7)], estimated=True)
    (_, _, _, _, at), = r["pads"]
    assert 1.0 <= at <= 1.2


def _write(path, segs):
    sf.write(str(path), np.concatenate(segs), SR, subtype="PCM_16")


def test_apply_pacing_shifts_cues_and_durations(tmp_path):
    wav = tmp_path / "v.wav"
    seg0 = np.concatenate([_sil(0.1), _tone(1.0), _sil(0.15), _tone(1.0), _sil(0.1)])
    seg1 = np.concatenate([_tone(1.0), _sil(0.05)])
    _write(wav, [seg0, seg1])
    plans = pacing.plan_segments([
        {"text": "One two three. Four five six.", "cue_text": "[[1]] One two three. [[2]] Four five six."},
        {"text": "Seven eight."},
    ])
    assert plans[0]["points"] == [(15, "sentence")]
    durs, cues, metas = pacing.apply_pacing(
        wav, [len(seg0) / SR, len(seg1) / SR], [[0.1, 1.25], []], [[1.25], []], plans, RELAXED,
        estimated=False)
    assert cues[0][0] == pytest.approx(0.5, abs=0.02)       # lead-in only
    assert cues[0][1] == pytest.approx(2.15, abs=0.02)      # lead-in + sentence pause
    assert durs[0] == pytest.approx(5.15, abs=0.03)
    assert metas[0]["hold_sec"] == pytest.approx(2.0, abs=0.02)
    assert metas[1]["hold_sec"] == pytest.approx(2.0, abs=0.02)   # the video ends on a hold too
    assert metas[0]["pauses"][0]["kind"] == "sentence"
    assert metas[0]["pauses"][0]["at"] < cues[0][1]
    got, sr = sf.read(str(wav))
    assert len(got) / sr == pytest.approx(sum(durs), abs=0.002)
    anchors = metas[0]["anchors"]
    assert anchors[0][0] == 0 and anchors[-1] == [len(plans[0]["clean"]), metas[0]["speech_end_sec"]]
    assert all(a[1] <= b[1] for a, b in zip(anchors, anchors[1:]))


def test_estimate_segment_includes_hold_and_pauses():
    seg = {"text": "Why? Because.", "cue_text": "Why? [[1]] Because.", "estimated_duration_sec": 2.0}
    est = pacing.estimate_segment(seg, RELAXED)
    speech = 2.0 / 0.94
    pad = RELAXED.question - pacing._NATURAL_GAP_EST
    assert est["actual_duration_sec"] == pytest.approx(RELAXED.lead_in + speech + pad + RELAXED.hold, abs=0.01)
    assert est["speech_end_sec"] == pytest.approx(est["actual_duration_sec"] - RELAXED.hold, abs=0.01)
    assert est["cues"][0] > RELAXED.lead_in + pad


def test_estimate_keeps_measured_segments():
    seg = {"text": "x", "actual_duration_sec": 3.0, "cues": [1.0], "speech_end_sec": 1.2}
    assert pacing.estimate_segment(seg, RELAXED) == {
        "text": "x", "actual_duration_sec": 3.0, "cues": [1.0], "speech_end_sec": 1.2}


# ── render_trigger integration ─────────────────────────────────────────────

def test_render_trigger_paces_the_voiceover(base_state, tmp_path, monkeypatch):
    from pipeline.render_trigger import render_trigger
    for k in ("PACE", "SCENE_HOLD_S", "PACE_SPEECH_SPEED"):
        monkeypatch.delenv(k, raising=False)
    base_state.update({
        "manim_code": "x = 1", "script": "Why? Because. [[beat]]", "run_id": "pace-run",
        "script_segments": [{"text": "Why? Because.", "cue_text": "Why? [[1]] Because. [[beat]]",
                             "estimated_duration_sec": 2.0}],
        "pace": "relaxed",
    })
    seen = {}

    async def backend(segments, output_path, speed=1.0):
        seen["cue_text"] = segments[0]["cue_text"]
        seen["speed"] = speed
        audio = np.concatenate([_sil(0.05), _tone(0.6), _sil(0.1), _tone(0.8), _sil(0.05)])
        sf.write(str(output_path), audio, SR, subtype="PCM_16")
        # cue [[1]] and the pause marker [[2]] both start "Because" at 0.75 s
        return output_path, [len(audio) / SR], [[0.75, 0.75]]

    monkeypatch.setattr("pipeline.render_trigger.OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr("pipeline.render_trigger.get_backend", lambda name: backend)
    asyncio.run(render_trigger(base_state))

    assert parse_cues(seen["cue_text"]) == ("Why? Because.", {1: 5, 2: 5})
    assert seen["speed"] == 0.94
    seg = json.loads((tmp_path / "pace-run" / "segments.json").read_text())[0]
    assert seg["cues"][0] == pytest.approx(0.75 + 0.45 + (RELAXED.question - 0.1), abs=0.02)
    # A closing [[beat]] makes the hold at least a beat; relaxed's hold is longer anyway.
    assert seg["hold_sec"] == pytest.approx(max(RELAXED.hold, RELAXED.beat), abs=0.02)
    assert seg["pauses"][0]["kind"] == "question"
    manifest = json.loads((tmp_path / "pace-run" / "manifest.json").read_text())
    assert manifest["pace"] == "relaxed" and manifest["pacing"]["applied"] is True
    assert manifest["pacing"]["effective_speed"] == 0.94


# ── native delivery speed ──────────────────────────────────────────────────

def _el_client(handler):
    import httpx
    from unittest.mock import patch
    from pipeline.tts import elevenlabs_tts
    real = httpx.Client
    return patch.object(elevenlabs_tts.httpx, "Client",
                        side_effect=lambda *a, **k: real(*a, **{**k, "transport": httpx.MockTransport(handler)}))


def _el_handler(bodies, settings_status=200):
    import base64
    import httpx

    def handler(request):
        if request.method == "GET":
            assert request.url.path.endswith("/settings")
            return httpx.Response(settings_status, json={"stability": 0.4, "similarity_boost": 0.8,
                                                         "style": 0.0, "use_speaker_boost": True, "speed": 1.0})
        body = json.loads(request.content)
        bodies.append(body)
        pcm = b"\x00\x00" * 24000
        chars = list(body["text"])
        return httpx.Response(200, json={"audio_base64": base64.b64encode(pcm).decode(), "alignment": {
            "characters": chars, "character_start_times_seconds": [i * 0.01 for i in range(len(chars))],
            "character_end_times_seconds": [i * 0.01 + 0.01 for i in range(len(chars))]}})
    return handler


def test_elevenlabs_uses_native_speed_with_stored_settings(tmp_path, monkeypatch):
    from pipeline.tts import elevenlabs_tts
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    bodies = []
    with _el_client(_el_handler(bodies)):
        _, durs, _ = asyncio.run(elevenlabs_tts.generate_audio(
            [{"text": "Hi there."}], tmp_path / "v.wav", speed=0.94, model="eleven_v4"))
    assert bodies[0]["voice_settings"] == {"stability": 0.4, "similarity_boost": 0.8, "style": 0.0,
                                           "use_speaker_boost": True, "speed": 0.94}
    assert durs == [1.0]          # no time-stretch afterwards


def test_elevenlabs_falls_back_to_atempo_without_settings(tmp_path, monkeypatch):
    from unittest.mock import patch
    from pipeline.tts import elevenlabs_tts
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    bodies = []
    with _el_client(_el_handler(bodies, settings_status=500)), \
            patch.object(elevenlabs_tts, "_apply_speed_to_wav") as stretch:
        _, durs, _ = asyncio.run(elevenlabs_tts.generate_audio(
            [{"text": "Hi there."}], tmp_path / "v.wav", speed=0.94, model="eleven_v4"))
    assert "voice_settings" not in bodies[0]       # never a partial override
    stretch.assert_called_once()
    assert durs == [pytest.approx(1.0 / 0.94)]


def test_elevenlabs_speed_one_sends_no_settings(tmp_path, monkeypatch):
    from pipeline.tts import elevenlabs_tts
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    bodies = []
    with _el_client(_el_handler(bodies)):
        asyncio.run(elevenlabs_tts.generate_audio([{"text": "Hi."}], tmp_path / "v.wav", model="eleven_v4"))
    assert "voice_settings" not in bodies[0]


def test_kokoro_passes_native_speed(tmp_path):
    from unittest.mock import patch
    from pipeline.tts import kokoro_tts
    calls = []

    class _R:
        tokens = []
        def __iter__(self):
            return iter(("x", "", np.zeros(2400, dtype=np.float32)))

    def pipe(text, voice, **kw):
        calls.append(kw)
        yield _R()

    with patch.object(kokoro_tts, "KPipeline", object), patch.object(kokoro_tts, "_pipeline", lambda: pipe):
        _, durs, _ = asyncio.run(kokoro_tts.generate_audio([{"text": "Hi."}], tmp_path / "v.wav", speed=0.94))
    assert calls == [{"speed": 0.94}]
    assert durs == [pytest.approx(0.1)]          # the audio is used as generated


# ── downstream: captions and timeline ──────────────────────────────────────

def test_captions_follow_pacing_anchors():
    from main import _caption_cues
    seg = {"text": "Why? Because.", "actual_duration_sec": 6.0, "cues": [],
           "speech_start_sec": 0.5, "speech_end_sec": 3.5,
           "anchors": [[0, 0.5], [4, 1.0], [5, 2.0], [13, 3.5]]}
    lines = _caption_cues([seg])
    assert lines[0][0] == pytest.approx(0.5)            # appears with the first word
    assert lines[1][0] == pytest.approx(2.0)            # second sentence after the pause
    assert lines[1][1] == pytest.approx(4.1)            # clears 0.6 s into the hold


def test_timeline_exposes_speech_end_and_pauses():
    from server.run_data import segments_timeline
    out = segments_timeline([{"text": "a", "actual_duration_sec": 4.0, "cues": [],
                              "speech_end_sec": 2.0,
                              "pauses": [{"at": 1.0, "sec": 0.4, "gap": 0.6, "kind": "question"}]},
                             {"text": "old run", "actual_duration_sec": 2.0}])
    assert out[0]["speech_end_s"] == 2.0
    assert out[0]["pauses"] == [{"at_s": 1.0, "sec": 0.4, "kind": "question"}]
    assert out[1]["speech_end_s"] is None and out[1]["pauses"] == []
