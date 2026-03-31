# tests/test_context.py
import pytest
from pathlib import Path
from pipeline.context import collect_files


def test_collect_files_single_file(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("hello")
    result = collect_files([str(f)])
    assert result == [f.resolve()]


def test_collect_files_directory_recursive(tmp_path):
    (tmp_path / "a.py").write_text("x")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.py").write_text("y")
    result = collect_files([str(tmp_path)])
    names = [p.name for p in result]
    assert "a.py" in names
    assert "b.py" in names


def test_collect_files_respects_gitignore(tmp_path):
    (tmp_path / ".gitignore").write_text("*.log\n")
    (tmp_path / "main.py").write_text("code")
    (tmp_path / "debug.log").write_text("log content")
    result = collect_files([str(tmp_path)])
    names = [p.name for p in result]
    assert "main.py" in names
    assert "debug.log" not in names


def test_collect_files_respects_extra_ignore_patterns(tmp_path):
    (tmp_path / "main.py").write_text("code")
    (tmp_path / "test_main.py").write_text("tests")
    result = collect_files([str(tmp_path)], ignore_patterns=["test_*.py"])
    names = [p.name for p in result]
    assert "main.py" in names
    assert "test_main.py" not in names


def test_collect_files_skips_hidden_directories(tmp_path):
    hidden = tmp_path / ".git"
    hidden.mkdir()
    (hidden / "config").write_text("git config")
    (tmp_path / "main.py").write_text("code")
    result = collect_files([str(tmp_path)])
    assert not any(".git" in str(p) for p in result)
    assert any(p.name == "main.py" for p in result)


def test_collect_files_raises_on_missing_path():
    with pytest.raises(FileNotFoundError, match="Context path not found"):
        collect_files(["/nonexistent/path/does_not_exist.txt"])


def test_collect_files_warns_on_empty_directory(tmp_path, capsys):
    (tmp_path / ".gitignore").write_text("*\n")
    (tmp_path / "ignored.txt").write_text("content")
    result = collect_files([str(tmp_path)])
    assert result == []
    captured = capsys.readouterr()
    assert "Warning" in captured.out


def test_collect_files_deduplicates_when_same_path_passed_twice(tmp_path):
    f = tmp_path / "file.py"
    f.write_text("x")
    result = collect_files([str(f), str(f)])
    assert len(result) == 1


import base64
from unittest.mock import MagicMock, patch
from pipeline.context import load_context_blocks, measure_context


# ---------------------------------------------------------------------------
# load_context_blocks
# ---------------------------------------------------------------------------

def test_load_context_blocks_text_file_produces_label_and_text(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("hello world")
    blocks = load_context_blocks([f])
    assert len(blocks) == 2
    assert blocks[0]["type"] == "text"
    assert "notes.txt" in blocks[0]["text"]
    assert blocks[0]["text"].startswith("--- file:")
    assert blocks[1] == {"type": "text", "text": "hello world"}


def test_load_context_blocks_python_file(tmp_path):
    f = tmp_path / "script.py"
    f.write_text("def foo(): pass")
    blocks = load_context_blocks([f])
    assert any("def foo" in b.get("text", "") for b in blocks)


def test_load_context_blocks_image_png(tmp_path):
    f = tmp_path / "image.png"
    raw = b"\x89PNG\r\n\x1a\n"
    f.write_bytes(raw)
    blocks = load_context_blocks([f])
    image_block = next(b for b in blocks if b.get("type") == "image")
    assert image_block["source"]["media_type"] == "image/png"
    assert image_block["source"]["type"] == "base64"
    assert image_block["source"]["data"] == base64.standard_b64encode(raw).decode()


def test_load_context_blocks_image_jpeg_media_type(tmp_path):
    f = tmp_path / "photo.jpg"
    f.write_bytes(b"\xff\xd8\xff")
    blocks = load_context_blocks([f])
    image_block = next(b for b in blocks if b.get("type") == "image")
    assert image_block["source"]["media_type"] == "image/jpeg"


def test_load_context_blocks_pdf_file(tmp_path):
    f = tmp_path / "paper.pdf"
    raw = b"%PDF-1.4"
    f.write_bytes(raw)
    blocks = load_context_blocks([f])
    doc_block = next(b for b in blocks if b.get("type") == "document")
    assert doc_block["source"]["media_type"] == "application/pdf"
    assert doc_block["source"]["data"] == base64.standard_b64encode(raw).decode()


def test_load_context_blocks_docx_extracts_text(tmp_path):
    f = tmp_path / "notes.docx"
    mock_doc = MagicMock()
    mock_doc.paragraphs = [
        MagicMock(text="First paragraph"),
        MagicMock(text="Second paragraph"),
        MagicMock(text=""),  # empty — should be filtered
    ]
    with patch("pipeline.context.DocxDocument", return_value=mock_doc):
        blocks = load_context_blocks([f])
    combined = " ".join(b.get("text", "") for b in blocks)
    assert "First paragraph" in combined
    assert "Second paragraph" in combined


def test_load_context_blocks_unsupported_extension_skipped(tmp_path, capsys):
    f = tmp_path / "data.xyz"
    f.write_bytes(b"\x00\x01\x02")
    blocks = load_context_blocks([f])
    assert blocks == []
    captured = capsys.readouterr()
    assert "Warning: skipping unsupported file type" in captured.out


def test_load_context_blocks_multiple_files(tmp_path):
    a = tmp_path / "a.txt"
    a.write_text("aaa")
    b = tmp_path / "b.txt"
    b.write_text("bbb")
    blocks = load_context_blocks([a, b])
    text_contents = [bl.get("text", "") for bl in blocks]
    assert any("a.txt" in t for t in text_contents)
    assert any("b.txt" in t for t in text_contents)
    assert any("aaa" in t for t in text_contents)
    assert any("bbb" in t for t in text_contents)


# ---------------------------------------------------------------------------
# measure_context
# ---------------------------------------------------------------------------

def test_measure_context_returns_token_count_and_window():
    mock_client = MagicMock()
    mock_client.messages.count_tokens.return_value = MagicMock(input_tokens=42000)
    mock_client.models.retrieve.return_value = MagicMock(max_input_tokens=200000)

    blocks = [{"type": "text", "text": "hello"}]
    token_count, context_window = measure_context(blocks, mock_client)

    assert token_count == 42000
    assert context_window == 200000


def test_measure_context_calls_correct_api():
    from config import CLAUDE_MODEL
    mock_client = MagicMock()
    mock_client.messages.count_tokens.return_value = MagicMock(input_tokens=100)
    mock_client.models.retrieve.return_value = MagicMock(max_input_tokens=200000)

    blocks = [{"type": "text", "text": "test"}]
    measure_context(blocks, mock_client)

    mock_client.messages.count_tokens.assert_called_once_with(
        model=CLAUDE_MODEL,
        messages=[{"role": "user", "content": blocks}],
    )
    mock_client.models.retrieve.assert_called_once_with(CLAUDE_MODEL)
