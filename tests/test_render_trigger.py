# tests/test_render_trigger.py
import json
import pytest
import asyncio
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock
from pipeline.render_trigger import render_trigger


SAMPLE_CODE = "from manim import *\nclass ChalkboardScene(Scene):\n    def construct(self): pass"


def test_render_trigger_writes_all_output_files(base_state, tmp_path):
    base_state["manim_code"] = SAMPLE_CODE
    base_state["script"] = "Hello world."
    base_state["script_segments"] = [{"text": "Hello.", "estimated_duration_sec": 1.0}]
    base_state["run_id"] = "test-run-001"

    async def mock_generate(segments, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"\x00" * 100)
        return output_path, [1.05]

    with patch("pipeline.render_trigger.OUTPUT_DIR", str(tmp_path)), \
         patch("pipeline.render_trigger.get_backend", return_value=mock_generate):
        result = asyncio.run(render_trigger(base_state))

    run_dir = tmp_path / "test-run-001"
    assert (run_dir / "scene.py").exists()
    assert (run_dir / "voiceover.wav").exists()
    assert (run_dir / "segments.json").exists()
    assert (run_dir / "script.txt").exists()
    assert (run_dir / "manifest.json").exists()
    assert result["status"] == "approved"
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["scene_class_name"] == "ChalkboardScene"
    assert manifest["run_id"] == "test-run-001"
    assert manifest["topic"] == base_state["topic"]


def test_render_trigger_segments_json_uses_actual_durations(base_state, tmp_path):
    base_state["manim_code"] = SAMPLE_CODE
    base_state["script"] = "Hello."
    base_state["script_segments"] = [{"text": "Hello.", "estimated_duration_sec": 1.0}]
    base_state["run_id"] = "run-dur-test"

    async def mock_generate(segments, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"\x00")
        return output_path, [2.73]  # actual duration differs from estimate

    with patch("pipeline.render_trigger.OUTPUT_DIR", str(tmp_path)), \
         patch("pipeline.render_trigger.get_backend", return_value=mock_generate):
        asyncio.run(render_trigger(base_state))

    segments = json.loads((tmp_path / "run-dur-test" / "segments.json").read_text())
    assert segments[0]["actual_duration_sec"] == pytest.approx(2.73)
