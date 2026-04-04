# server/upload.py
from __future__ import annotations
from pathlib import Path
from fastapi import UploadFile
from pipeline.context import TEXT_EXTENSIONS, IMAGE_MEDIA_TYPES

# Per-file size limits (bytes) by category
LIMITS: dict[str, int] = {
    "text":  2 * 1024 * 1024,   # 2 MB
    "image": 5 * 1024 * 1024,   # 5 MB
    "pdf":  20 * 1024 * 1024,   # 20 MB
    "docx": 10 * 1024 * 1024,   # 10 MB
}
TOTAL_LIMIT: int = 24 * 1024 * 1024  # 24 MB across all files


class UploadValidationError(ValueError):
    """Base class for upload validation failures."""


class UnsupportedFileTypeError(UploadValidationError):
    """File extension not supported by the pipeline."""


class FileSizeError(UploadValidationError):
    """Single file exceeds its per-type size limit."""


class TotalSizeError(UploadValidationError):
    """Sum of all uploaded files exceeds the total size limit."""


def file_category(filename: str) -> str:
    """
    Return the category string used for limit lookup.
    Returns 'unsupported' for unrecognised extensions.
    """
    ext = Path(filename).suffix.lower()
    if ext in IMAGE_MEDIA_TYPES:
        return "image"
    if ext == ".pdf":
        return "pdf"
    if ext == ".docx":
        return "docx"
    if ext in TEXT_EXTENSIONS:
        return "text"
    return "unsupported"


async def validate_and_save(files: list[UploadFile], tmp_dir: Path) -> list[Path]:
    """
    Validate each UploadFile against per-type and total size limits,
    then write accepted files to tmp_dir. Returns list of saved Paths.

    Raises:
        UnsupportedFileTypeError: for unrecognised file extensions
        FileSizeError: when a single file exceeds its category limit
        TotalSizeError: when the running total exceeds TOTAL_LIMIT
    """
    total = 0
    saved: list[Path] = []

    for upload in files:
        filename = Path(upload.filename or "").name or f"file_{len(saved)}"
        category = file_category(filename)

        if category == "unsupported":
            raise UnsupportedFileTypeError(
                f"{filename}: unsupported file type. "
                f"Supported types: text/code files, .pdf, .docx, .png, .jpg, .gif, .webp"
            )

        data = await upload.read()
        size = len(data)
        limit = LIMITS[category]

        if size > limit:
            limit_mb = limit / (1024 * 1024)
            size_mb = size / (1024 * 1024)
            raise FileSizeError(
                f"{filename}: {size_mb:.1f} MB exceeds the {limit_mb:.0f} MB "
                f"limit for {category} files"
            )

        total += size
        if total > TOTAL_LIMIT:
            total_mb = TOTAL_LIMIT / (1024 * 1024)
            raise TotalSizeError(
                f"Total upload size exceeds the {total_mb:.0f} MB limit"
            )

        # Resolve filename conflicts
        dest = tmp_dir / filename
        if dest.exists():
            stem = Path(filename).stem
            suffix = Path(filename).suffix
            counter = 1
            while dest.exists():
                dest = tmp_dir / f"{stem}_{counter}{suffix}"
                counter += 1

        dest.write_bytes(data)
        saved.append(dest)

    return saved
