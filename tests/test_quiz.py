"""Tests for _generate_quiz in main.py."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from main import _generate_quiz


FAKE_QUESTIONS = [
    {
        "question": "What is quicksort?",
        "options": ["A) A search algorithm", "B) A sorting algorithm", "C) A data structure", "D) A graph algorithm"],
        "answer": "B",
        "explanation": "Quicksort is a divide-and-conquer sorting algorithm.",
    },
    {
        "question": "What is the average time complexity of quicksort?",
        "options": ["A) O(n)", "B) O(n²)", "C) O(n log n)", "D) O(log n)"],
        "answer": "C",
        "explanation": "On average, quicksort runs in O(n log n) time.",
    },
]


def _make_mock_response(questions):
    response = MagicMock()
    response.content = [MagicMock()]
    response.content[0].text = json.dumps({"questions": questions})
    return response


def test_generates_quiz_json(tmp_path, monkeypatch):
    run_id = "test-run-123"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    (run_dir / "script.txt").write_text("Quicksort is a sorting algorithm.")

    monkeypatch.setattr("main.OUTPUT_DIR", str(tmp_path))

    mock_client = MagicMock()
    mock_client.messages.create.return_value = _make_mock_response(FAKE_QUESTIONS)

    with patch("anthropic.Anthropic", return_value=mock_client):
        result = _generate_quiz(run_id)

    assert result == run_dir / "quiz.json"
    written = json.loads((run_dir / "quiz.json").read_text())
    assert len(written) == 2
    assert written[0]["question"] == "What is quicksort?"
    assert written[0]["answer"] == "B"


def test_returns_none_when_no_script(tmp_path, monkeypatch):
    run_id = "test-run-empty"
    (tmp_path / run_id).mkdir()
    monkeypatch.setattr("main.OUTPUT_DIR", str(tmp_path))

    with patch("anthropic.Anthropic"):
        result = _generate_quiz(run_id)

    assert result is None


def test_quiz_json_structure(tmp_path, monkeypatch):
    run_id = "test-run-structure"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    (run_dir / "script.txt").write_text("Binary search halves the search space each step.")

    monkeypatch.setattr("main.OUTPUT_DIR", str(tmp_path))

    mock_client = MagicMock()
    mock_client.messages.create.return_value = _make_mock_response(FAKE_QUESTIONS)

    with patch("anthropic.Anthropic", return_value=mock_client):
        _generate_quiz(run_id)

    questions = json.loads((run_dir / "quiz.json").read_text())
    for q in questions:
        assert "question" in q
        assert "options" in q
        assert "answer" in q
        assert "explanation" in q
        assert isinstance(q["options"], list)


def test_passes_script_to_claude(tmp_path, monkeypatch):
    run_id = "test-run-content"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    script_text = "This is my test script about hash tables."
    (run_dir / "script.txt").write_text(script_text)

    monkeypatch.setattr("main.OUTPUT_DIR", str(tmp_path))

    mock_client = MagicMock()
    mock_client.messages.create.return_value = _make_mock_response(FAKE_QUESTIONS)

    with patch("anthropic.Anthropic", return_value=mock_client):
        _generate_quiz(run_id)

    call_args = mock_client.messages.create.call_args
    user_content = call_args.kwargs["messages"][0]["content"]
    assert script_text in user_content
