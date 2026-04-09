# tests/test_layout_checker.py
import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


def _base_state(run_id="test-run", code_attempts=0):
    return {
        "run_id": run_id,
        "manim_code": "from manim import *\nclass ChalkboardScene(ChalkboardSceneBase, Scene): pass",
        "code_attempts": code_attempts,
        "script": "Test script.",
        "topic": "Test",
    }


def _write_report(run_dir: Path, passed: bool, violations=None):
    report = {"passed": passed, "violations": violations or []}
    (run_dir / "layout_report.json").write_text(json.dumps(report))


def _mock_proc(run_dir: Path, passed: bool, violations=None, stderr: bytes = b""):
    """
    Create a mock subprocess whose communicate() writes layout_report.json.
    This simulates ChalkboardSceneBase writing the report during the Docker run,
    AFTER layout_checker has already deleted any stale report via unlink().
    """
    async def _communicate():
        _write_report(run_dir, passed=passed, violations=violations)
        return b"", stderr

    mock = MagicMock()
    mock.communicate = AsyncMock(side_effect=_communicate)
    return mock


async def _run(state):
    from pipeline.agents.layout_checker import layout_checker
    return await layout_checker(state)


def test_layout_checker_returns_none_feedback_on_pass(tmp_path):
    state = _base_state(run_id="run1")
    run_dir = tmp_path / "run1"
    run_dir.mkdir()

    with patch("pipeline.agents.layout_checker.OUTPUT_DIR", str(tmp_path)), \
         patch("asyncio.create_subprocess_exec",
               new=AsyncMock(return_value=_mock_proc(run_dir, passed=True))):
        result = asyncio.run(_run(state))

    assert result["code_feedback"] is None


def test_layout_checker_returns_feedback_on_violations(tmp_path):
    state = _base_state(run_id="run2", code_attempts=0)
    run_dir = tmp_path / "run2"
    run_dir.mkdir()

    violations = [
        {
            "type": "timing_overrun",
            "segment": 2,
            "budget_sec": 3.0,
            "actual_sec": 5.0,
            "description": "Segment 2 animations take 5.0s but audio budget is 3.0s (2.0s over)",
        }
    ]

    with patch("pipeline.agents.layout_checker.OUTPUT_DIR", str(tmp_path)), \
         patch("asyncio.create_subprocess_exec",
               new=AsyncMock(return_value=_mock_proc(run_dir, passed=False, violations=violations))):
        result = asyncio.run(_run(state))

    assert result["code_feedback"] is not None
    assert "Segment 2" in result["code_feedback"]
    assert "TIMING OVERRUN" in result["code_feedback"]
    assert result["code_attempts"] == 1


def test_layout_checker_increments_attempts_on_violation(tmp_path):
    state = _base_state(run_id="run3", code_attempts=1)
    run_dir = tmp_path / "run3"
    run_dir.mkdir()

    violations = [{"type": "overlap", "segment": 0, "objects": ["A", "B"],
                   "overlap_region": {"x": [0, 1], "y": [0, 1]},
                   "description": "Segment 0: A and B partially overlap"}]

    with patch("pipeline.agents.layout_checker.OUTPUT_DIR", str(tmp_path)), \
         patch("asyncio.create_subprocess_exec",
               new=AsyncMock(return_value=_mock_proc(run_dir, passed=False, violations=violations))):
        result = asyncio.run(_run(state))

    assert result["code_attempts"] == 2


def test_layout_checker_handles_missing_report(tmp_path):
    """If report file not written (scene crashed), feedback contains the stderr."""
    state = _base_state(run_id="run4")
    run_dir = tmp_path / "run4"
    run_dir.mkdir()

    async def _communicate_no_report():
        # Do NOT write report — simulates scene crash before end_layout_check()
        return b"", b"NameError: name 'foo' is not defined"

    mock = MagicMock()
    mock.communicate = AsyncMock(side_effect=_communicate_no_report)

    with patch("pipeline.agents.layout_checker.OUTPUT_DIR", str(tmp_path)), \
         patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock)):
        result = asyncio.run(_run(state))

    assert result["code_feedback"] is not None
    assert "crashed" in result["code_feedback"].lower() or "report" in result["code_feedback"].lower()
    assert result["code_attempts"] == 1


def test_layout_checker_handles_timeout(tmp_path):
    state = _base_state(run_id="run5")
    (tmp_path / "run5").mkdir()

    async def _slow_create(*args, **kwargs):
        raise asyncio.TimeoutError()

    with patch("pipeline.agents.layout_checker.OUTPUT_DIR", str(tmp_path)), \
         patch("asyncio.create_subprocess_exec", new=_slow_create):
        result = asyncio.run(_run(state))

    assert result["code_feedback"] is not None
    assert "timed out" in result["code_feedback"].lower()
    assert result["code_attempts"] == 1


def test_layout_checker_stale_report_not_used(tmp_path):
    """Stale passing report from a previous attempt must not contaminate current run."""
    state = _base_state(run_id="run6")
    run_dir = tmp_path / "run6"
    run_dir.mkdir()

    # Write stale PASSING report — simulate leftover from a previous attempt
    _write_report(run_dir, passed=True)

    violations = [{"type": "off_screen", "segment": 1, "object": "Text",
                   "description": "Segment 1: Text extends outside canvas"}]

    # communicate() writes a FAILING report (overrides stale one after unlink)
    with patch("pipeline.agents.layout_checker.OUTPUT_DIR", str(tmp_path)), \
         patch("asyncio.create_subprocess_exec",
               new=AsyncMock(return_value=_mock_proc(run_dir, passed=False, violations=violations))):
        result = asyncio.run(_run(state))

    # Must report the failure from the new run, not the stale pass
    assert result["code_feedback"] is not None
    assert "off_screen" in result["code_feedback"].lower() or "OFF SCREEN" in result["code_feedback"]
