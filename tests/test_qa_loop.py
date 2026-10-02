# tests/test_qa_loop.py
"""Visual-QA regeneration loop in main.py (no Claude, no render)."""
import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import main


def _make_run(tmp_path: Path, **manifest_extra) -> Path:
    run_dir = tmp_path / "run1"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(json.dumps({
        "run_id": "run1", "topic": "t", "quality": "low", **manifest_extra,
    }))
    (run_dir / "script.txt").write_text("script")
    (run_dir / "segments.json").write_text(json.dumps([{"text": "a", "actual_duration_sec": 3.0}]))
    (run_dir / "scene.py").write_text("# original scene\n")
    return run_dir


def test_qa_regenerate_uses_manifest_settings(tmp_path, monkeypatch):
    """A resumed run passes CLI defaults; the regenerated scene must keep the
    theme and template the run was made with (read from manifest.json)."""
    monkeypatch.setattr(main, "OUTPUT_DIR", str(tmp_path))
    run_dir = _make_run(tmp_path, theme="light", template="derivation",
                        audience="expert", tone="formal", effort="high")
    seen = {}

    async def fake_manim_agent(state, context_blocks=None):
        seen.update(state)
        return {"manim_code": "def broken(:\n"}   # fails the static checks

    with patch("pipeline.agents.manim_agent.manim_agent", new=fake_manim_agent):
        ok = asyncio.run(main._qa_regenerate_scene(
            "run1", "[error] overlap", theme="chalkboard", audience="beginner",
            tone="casual", effort_level="low",
        ))

    assert ok is False
    assert seen["theme"] == "light"
    assert seen["template"] == "derivation"
    assert seen["audience"] == "expert"
    assert seen["tone"] == "formal"
    assert seen["effort_level"] == "high"
    assert seen["script_segments"][0]["estimated_duration_sec"] == 3.0
    assert (run_dir / "scene.py").read_text() == "# original scene\n"


def test_qa_regenerate_falls_back_to_arguments_for_old_manifests(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "OUTPUT_DIR", str(tmp_path))
    _make_run(tmp_path)   # manifest without theme/template fields
    seen = {}

    async def fake_manim_agent(state, context_blocks=None):
        seen.update(state)
        return {"manim_code": "def broken(:\n"}

    with patch("pipeline.agents.manim_agent.manim_agent", new=fake_manim_agent):
        asyncio.run(main._qa_regenerate_scene(
            "run1", "[error] x", theme="colorful", audience="beginner",
            tone="socratic", effort_level="medium",
        ))

    assert seen["theme"] == "colorful"
    assert seen["tone"] == "socratic"
    assert seen["template"] is None


def test_qa_loop_rerender_keeps_burn_captions(tmp_path, monkeypatch):
    """The QA re-render must burn captions in again when the first render did."""
    monkeypatch.setattr(main, "OUTPUT_DIR", str(tmp_path))
    run_dir = _make_run(tmp_path)
    final = run_dir / "final.mp4"
    final.write_bytes(b"v1")

    qa_results = iter([
        {"passed": False, "issues": [{"severity": "error", "description": "overlap"}]},
        {"passed": True, "issues": []},
    ])
    render_calls = []

    def fake_render(run_id, verbose=False, burn_captions=False):
        render_calls.append(burn_captions)
        final.write_bytes(b"v2")
        return final

    async def fake_regen(*args, **kwargs):
        return True

    with patch("main._run_visual_qa", side_effect=lambda *a, **k: next(qa_results)), \
         patch("main._qa_regenerate_scene", new=fake_regen), \
         patch("main._render", new=fake_render):
        main._run_qa_loop("run1", final, theme="chalkboard", audience="beginner",
                          tone="casual", effort_level="low", burn_captions=True)

    assert render_calls == [True]
    assert final.read_bytes() == b"v2"
    assert not (run_dir / "final.prev.mp4").exists()
