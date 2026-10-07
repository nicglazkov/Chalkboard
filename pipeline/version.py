# pipeline/version.py
"""The running Chalkboard's version.

`VERSION` at the repo root is the single source of truth (no packaging
metadata). Git facts come from the checkout this code runs from and are
captured once per process; every git field is None when git or the repo is
not available, never a guess.
"""
from __future__ import annotations

import functools
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "VERSION"
CHANGELOG_FILE = ROOT / "CHANGELOG.md"

SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
GIT_TIMEOUT = 2.0


def read_version(path: Path = VERSION_FILE) -> str | None:
    """The version string in `path`, or None if missing, empty or not semver."""
    try:
        v = path.read_text().strip()
    except OSError:
        return None
    return v if SEMVER_RE.match(v) else None


__version__ = read_version()


def _git(root: Path, *args: str) -> str | None:
    if shutil.which("git") is None:
        return None
    try:
        r = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                           text=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def _same_path(a: str, b: Path) -> bool:
    try:
        return Path(a).resolve() == b.resolve()
    except OSError:
        return False


def git_info(root: Path = ROOT) -> dict:
    """{commit, short, date, branch, dirty} for the checkout at `root`.

    All None unless `root` is itself the top of a git work tree (so a copy
    nested inside some other repo never reports that repo's commit).
    branch is None on a detached HEAD; dirty means tracked files differ
    from HEAD (untracked files such as output/ do not count)."""
    info = {"commit": None, "short": None, "date": None, "branch": None, "dirty": None}
    top = _git(root, "rev-parse", "--show-toplevel")
    if not top or not _same_path(top, root):
        return info
    commit = _git(root, "rev-parse", "HEAD")
    if not commit:
        return info
    info["commit"] = commit
    info["short"] = _git(root, "rev-parse", "--short", "HEAD")
    info["date"] = _git(root, "log", "-1", "--format=%cI", "HEAD") or None
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    info["branch"] = branch if branch and branch != "HEAD" else None
    porcelain = _git(root, "status", "--porcelain", "--untracked-files=no")
    info["dirty"] = None if porcelain is None else bool(porcelain)
    return info


@functools.lru_cache(maxsize=1)
def git() -> dict:
    """git_info() for this checkout, captured once per process."""
    return git_info()


def stamp() -> dict:
    """What every run records about the code that produced it."""
    return {"chalkboard_version": __version__, "git_commit": git()["commit"]}


def version_string() -> str:
    """e.g. 'chalkboard 0.3.1 (1a2b3c4, modified)'."""
    g = git()
    extra = ", ".join(x for x in (g["short"], "modified" if g["dirty"] else None) if x)
    return f"chalkboard {__version__ or 'unknown'}" + (f" ({extra})" if extra else "")


_HEADING_RE = re.compile(r"^##\s+\[?([^\]\s]+)\]?(?:\s+-\s+(\S+))?\s*$")


def latest_changelog(path: Path = CHANGELOG_FILE) -> dict | None:
    """The newest released entry of CHANGELOG.md: {version, date, heading, items}.

    `## [Unreleased]` is skipped. items are the entry's bullet lines with the
    leading '- ' removed. None if the file is missing or has no entry."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None
    entry = None
    for line in lines:
        if line.startswith("## "):
            if entry is not None:
                break
            m = _HEADING_RE.match(line)
            if m and m.group(1).lower() != "unreleased":
                entry = {"version": m.group(1), "date": m.group(2),
                         "heading": line[3:].strip(), "items": []}
            continue
        if entry is not None and re.match(r"^\s*[-*]\s+", line) and not line.startswith(("  ", "\t")):
            entry["items"].append(re.sub(r"^[-*]\s+", "", line).strip())
    return entry


def changelog_has(version: str, path: Path = CHANGELOG_FILE) -> bool:
    """True if CHANGELOG.md has a `## [version]` heading."""
    try:
        text = path.read_text()
    except OSError:
        return False
    return re.search(rf"^##\s+\[?{re.escape(version)}\]?(\s|$)", text, re.M) is not None
