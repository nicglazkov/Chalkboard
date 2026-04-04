# tests/test_server_upload.py
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from server.upload import (
    file_category,
    validate_and_save,
    FileSizeError,
    TotalSizeError,
    UnsupportedFileTypeError,
    LIMITS,
    TOTAL_LIMIT,
)


def _mock_upload(filename: str, data: bytes) -> MagicMock:
    u = MagicMock()
    u.filename = filename
    u.read = AsyncMock(return_value=data)
    return u


# ── file_category ──────────────────────────────────────────────────────────

def test_file_category_text():
    assert file_category("script.py") == "text"
    assert file_category("README.md") == "text"
    assert file_category("data.json") == "text"
    assert file_category("style.css") == "text"


def test_file_category_image():
    assert file_category("photo.png") == "image"
    assert file_category("banner.jpg") == "image"
    assert file_category("anim.gif") == "image"
    assert file_category("img.webp") == "image"


def test_file_category_pdf():
    assert file_category("paper.pdf") == "pdf"
    assert file_category("PAPER.PDF") == "pdf"


def test_file_category_docx():
    assert file_category("doc.docx") == "docx"
    assert file_category("REPORT.DOCX") == "docx"


def test_file_category_unsupported():
    assert file_category("archive.zip") == "unsupported"
    assert file_category("binary.exe") == "unsupported"
    assert file_category("video.mp4") == "unsupported"
    assert file_category("noextension") == "unsupported"


# ── validate_and_save ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_validate_and_save_writes_file(tmp_path):
    data = b"print('hello')"
    saved = await validate_and_save([_mock_upload("hello.py", data)], tmp_path)
    assert len(saved) == 1
    assert saved[0].read_bytes() == data


@pytest.mark.asyncio
async def test_validate_and_save_deduplicates_filename(tmp_path):
    """Two uploads with the same filename must not overwrite each other."""
    saved = await validate_and_save(
        [_mock_upload("notes.md", b"first"), _mock_upload("notes.md", b"second")],
        tmp_path,
    )
    assert len(saved) == 2
    assert saved[0].read_bytes() != saved[1].read_bytes()


@pytest.mark.asyncio
async def test_validate_and_save_empty_list(tmp_path):
    saved = await validate_and_save([], tmp_path)
    assert saved == []


@pytest.mark.asyncio
async def test_validate_and_save_raises_for_unsupported(tmp_path):
    with pytest.raises(UnsupportedFileTypeError, match="archive.zip"):
        await validate_and_save([_mock_upload("archive.zip", b"data")], tmp_path)


@pytest.mark.asyncio
async def test_validate_and_save_raises_file_size_error_text(tmp_path):
    big = b"x" * (LIMITS["text"] + 1)
    with pytest.raises(FileSizeError, match="big.py"):
        await validate_and_save([_mock_upload("big.py", big)], tmp_path)


@pytest.mark.asyncio
async def test_validate_and_save_raises_file_size_error_image(tmp_path):
    big = b"x" * (LIMITS["image"] + 1)
    with pytest.raises(FileSizeError, match="photo.png"):
        await validate_and_save([_mock_upload("photo.png", big)], tmp_path)


@pytest.mark.asyncio
async def test_validate_and_save_raises_file_size_error_pdf(tmp_path):
    big = b"x" * (LIMITS["pdf"] + 1)
    with pytest.raises(FileSizeError, match="paper.pdf"):
        await validate_and_save([_mock_upload("paper.pdf", big)], tmp_path)


@pytest.mark.asyncio
async def test_validate_and_save_raises_total_size_error(tmp_path):
    # Two 13 MB PDFs: each under 20 MB limit but together exceed 24 MB total
    chunk = b"x" * (13 * 1024 * 1024)
    uploads = [_mock_upload(f"doc{i}.pdf", chunk) for i in range(2)]
    with pytest.raises(TotalSizeError):
        await validate_and_save(uploads, tmp_path)


@pytest.mark.asyncio
async def test_validate_and_save_at_exact_limit_passes(tmp_path):
    """A file exactly at its per-type limit must be accepted."""
    data = b"x" * LIMITS["pdf"]
    saved = await validate_and_save([_mock_upload("ok.pdf", data)], tmp_path)
    assert len(saved) == 1
