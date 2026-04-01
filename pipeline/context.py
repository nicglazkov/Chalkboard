# pipeline/context.py
import base64
import os
from pathlib import Path
from typing import Any

try:
    import pathspec as _pathspec
except ImportError:
    _pathspec = None  # type: ignore[assignment]

try:
    from docx import Document as DocxDocument
except ImportError:
    DocxDocument = None  # type: ignore[assignment,misc]

try:
    import httpx as _httpx
except ImportError:
    _httpx = None  # type: ignore[assignment]

try:
    from bs4 import BeautifulSoup as _BeautifulSoup
except ImportError:
    _BeautifulSoup = None  # type: ignore[assignment,misc]

TEXT_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs",
    ".java", ".c", ".cpp", ".c++", ".cc", ".cxx", ".h", ".hpp", ".hxx",
    ".rb", ".swift", ".kt", ".sh", ".bash", ".zsh", ".fish",
    ".yaml", ".yml", ".json", ".toml", ".csv",
    ".html", ".css", ".scss", ".xml", ".ini", ".env", ".sql", ".graphql",
    ".proto", ".tf", ".hcl", ".vue", ".php", ".scala", ".clj", ".hs",
    ".ml", ".r", ".lua", ".pl", ".ex", ".exs", ".erl", ".dart", ".elm",
    ".gitignore", ".editorconfig", ".dockerignore", ".makefile",
}

# Filenames with no extension that are always plain text
TEXT_FILENAMES = {
    "Makefile", "makefile", "GNUmakefile",
    "Dockerfile", "dockerfile",
    "LICENSE", "LICENCE", "LICENSE.md", "LICENCE.md",
    "README", "CHANGELOG", "AUTHORS", "CONTRIBUTING", "NOTICE",
    "Gemfile", "Rakefile", "Procfile",
    ".gitignore", ".dockerignore", ".editorconfig",
}

IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def collect_files(paths: list[str], ignore_patterns: list[str] | None = None) -> list[Path]:
    """
    Collect all files from paths (files or directories).
    Directories are walked recursively, respecting .gitignore files and ignore_patterns.
    Returns deduplicated list of resolved Paths.
    Raises FileNotFoundError if any path does not exist.
    """
    if _pathspec is None:
        raise ImportError("Install pathspec: pip install pathspec")

    ignore_patterns = ignore_patterns or []
    extra_spec = (
        _pathspec.PathSpec.from_lines("gitignore", ignore_patterns)
        if ignore_patterns else None
    )
    result: list[Path] = []

    for path_str in paths:
        p = Path(path_str).resolve()
        if not p.exists():
            raise FileNotFoundError(f"Context path not found: {path_str}")
        if p.is_file():
            result.append(p)
        else:
            collected = _walk_directory(p, extra_spec)
            if not collected:
                print(f"  Warning: no files found in {path_str} after filtering")
            result.extend(collected)

    # Deduplicate while preserving order
    seen: set[Path] = set()
    deduped: list[Path] = []
    for f in result:
        if f not in seen:
            seen.add(f)
            deduped.append(f)
    return deduped


def _walk_directory(root: Path, extra_spec) -> list[Path]:
    """Walk root recursively, respecting .gitignore at each directory level."""
    result: list[Path] = []
    gitignore_specs: dict[Path, Any] = {}

    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)

        # Skip hidden directories in-place (modifying dirnames stops os.walk descending)
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))

        # Load .gitignore for this directory if present
        gitignore_path = current / ".gitignore"
        if gitignore_path.exists():
            try:
                spec = _pathspec.PathSpec.from_lines(
                    "gitignore", gitignore_path.read_text().splitlines()
                )
                gitignore_specs[current] = spec
            except OSError:
                pass

        for filename in sorted(filenames):
            file_path = current / filename

            # Check gitignore specs from this dir and all parent dirs within root
            skip = False
            for ancestor, spec in gitignore_specs.items():
                try:
                    rel = file_path.relative_to(ancestor)
                    if spec.match_file(str(rel)):
                        skip = True
                        break
                except ValueError:
                    pass
            if skip:
                continue

            # Check extra ignore patterns relative to root
            if extra_spec:
                try:
                    rel = file_path.relative_to(root)
                    if extra_spec.match_file(str(rel)):
                        continue
                except ValueError:
                    pass

            try:
                file_path.stat()  # permission check
                result.append(file_path)
            except PermissionError:
                print(f"  Warning: cannot read {file_path} — skipping")

    return result


def load_context_blocks(files: list[Path]) -> list[dict]:
    """
    Convert a list of files to Anthropic content blocks.
    Each file gets a label block then a content block.
    Unsupported extensions are skipped with a warning.
    """
    if DocxDocument is None and any(f.suffix.lower() == ".docx" for f in files):
        raise ImportError("Install python-docx: pip install python-docx")

    blocks: list[dict] = []

    for file_path in files:
        ext = file_path.suffix.lower()

        if ext in TEXT_EXTENSIONS or file_path.name in TEXT_FILENAMES:
            blocks.append({"type": "text", "text": f"--- file: {file_path} ---"})
            blocks.append({"type": "text", "text": file_path.read_text(errors="replace")})

        elif ext in IMAGE_MEDIA_TYPES:
            data = base64.standard_b64encode(file_path.read_bytes()).decode()
            blocks.append({"type": "text", "text": f"--- file: {file_path} ---"})
            blocks.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": IMAGE_MEDIA_TYPES[ext],
                    "data": data,
                },
            })

        elif ext == ".pdf":
            data = base64.standard_b64encode(file_path.read_bytes()).decode()
            blocks.append({"type": "text", "text": f"--- file: {file_path} ---"})
            blocks.append({
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": data,
                },
            })

        elif ext == ".docx":
            doc = DocxDocument(str(file_path))
            text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            blocks.append({"type": "text", "text": f"--- file: {file_path} ---"})
            blocks.append({"type": "text", "text": text})

        else:
            print(f"  Warning: skipping unsupported file type: {file_path}")

    return blocks


def fetch_url_blocks(url: str) -> list[dict]:
    """
    Fetch a URL and return Anthropic content blocks (same format as load_context_blocks).
    HTML is stripped to plain text via BeautifulSoup. Content truncated at 100k chars.
    """
    if _httpx is None:
        raise ImportError("Install httpx: pip install httpx")
    if _BeautifulSoup is None:
        raise ImportError("Install beautifulsoup4: pip install beautifulsoup4")

    response = _httpx.get(url, follow_redirects=True, timeout=30)
    response.raise_for_status()

    content_type = response.headers.get("content-type", "")
    if "text/html" in content_type:
        soup = _BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
    else:
        text = response.text

    if len(text) > 100_000:
        text = text[:100_000] + "\n[... truncated]"

    return [
        {"type": "text", "text": f"--- url: {url} ---"},
        {"type": "text", "text": text},
    ]


def measure_context(blocks: list[dict], client) -> tuple[int, int]:
    """
    Returns (token_count, context_window) using the Anthropic API.
    Both values are fetched live — nothing is hardcoded.
    """
    from config import CLAUDE_MODEL

    response = client.messages.count_tokens(
        model=CLAUDE_MODEL,
        messages=[{"role": "user", "content": blocks}],
    )
    token_count = response.input_tokens

    model_info = client.models.retrieve(CLAUDE_MODEL)
    context_window = model_info.max_input_tokens

    return token_count, context_window
