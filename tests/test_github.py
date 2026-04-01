"""Tests for _github_to_raw_url in main.py."""
import pytest
from main import _github_to_raw_url


def test_owner_repo():
    assert _github_to_raw_url("nicglazkov/Chalkboard") == \
        "https://raw.githubusercontent.com/nicglazkov/Chalkboard/HEAD/README.md"


def test_full_https_url():
    assert _github_to_raw_url("https://github.com/nicglazkov/Chalkboard") == \
        "https://raw.githubusercontent.com/nicglazkov/Chalkboard/HEAD/README.md"


def test_url_with_git_suffix():
    assert _github_to_raw_url("https://github.com/nicglazkov/Chalkboard.git") == \
        "https://raw.githubusercontent.com/nicglazkov/Chalkboard/HEAD/README.md"


def test_url_with_trailing_slash():
    assert _github_to_raw_url("https://github.com/nicglazkov/Chalkboard/") == \
        "https://raw.githubusercontent.com/nicglazkov/Chalkboard/HEAD/README.md"


def test_url_with_tree_path():
    url = _github_to_raw_url("https://github.com/nicglazkov/Chalkboard/tree/main")
    assert url == "https://raw.githubusercontent.com/nicglazkov/Chalkboard/HEAD/README.md"


def test_invalid_no_slash():
    with pytest.raises(ValueError, match="Expected 'owner/repo'"):
        _github_to_raw_url("notarepo")


def test_invalid_too_many_slashes():
    with pytest.raises(ValueError):
        _github_to_raw_url("not/a/repo/format")
