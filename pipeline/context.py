# pipeline/context.py
import base64
import os
from pathlib import Path

try:
    import pathspec as _pathspec
except ImportError:
    _pathspec = None  # type: ignore[assignment]

try:
    from docx import Document as DocxDocument
except ImportError:
    DocxDocument = None  # type: ignore[assignment,misc]

TEXT_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs",
    ".java", ".c", ".cpp", ".h", ".hpp", ".rb", ".swift", ".kt", ".sh",
    ".bash", ".zsh", ".fish", ".yaml", ".yml", ".json", ".toml", ".csv",
    ".html", ".css", ".scss", ".xml", ".ini", ".env", ".sql", ".graphql",
    ".proto", ".tf", ".hcl", ".vue", ".php", ".scala", ".clj", ".hs",
    ".ml", ".r", ".lua", ".pl", ".ex", ".exs", ".erl", ".dart", ".elm",
    ".gitignore", ".editorconfig", ".dockerignore", ".makefile",
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
        _pathspec.PathSpec.from_lines("gitwildmatch", ignore_patterns)
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
    gitignore_specs: dict[Path, object] = {}

    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)

        # Skip hidden directories in-place (modifying dirnames stops os.walk descending)
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))

        # Load .gitignore for this directory if present
        gitignore_path = current / ".gitignore"
        if gitignore_path.exists():
            try:
                spec = _pathspec.PathSpec.from_lines(
                    "gitwildmatch", gitignore_path.read_text().splitlines()
                )
                gitignore_specs[current] = spec
            except Exception:
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
