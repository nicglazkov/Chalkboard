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
