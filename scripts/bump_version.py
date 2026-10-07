#!/usr/bin/env python3
"""Bump VERSION and prepend a CHANGELOG.md entry dated today.

    python scripts/bump_version.py patch "Fix the thing"
    python scripts/bump_version.py minor "Add pacing" "Second bullet"

Every change merged to main bumps the version (patch by default, minor for
notable features, major for breaking changes) and adds a changelog entry;
CI fails a pull request that does not.
"""
from __future__ import annotations
import argparse
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
TAG_URL = "https://github.com/nicglazkov/Chalkboard/tree/v{v}"


def bump(version: str, part: str) -> str:
    m = SEMVER.match(version.strip())
    if not m:
        raise ValueError(f"VERSION is not MAJOR.MINOR.PATCH: {version!r}")
    major, minor, patch = map(int, m.groups())
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def add_entry(changelog: str, version: str, items: list[str], today: str) -> str:
    """Insert `## [version] - today` + bullets above the newest entry, and a
    link reference above the existing ones."""
    entry = f"## [{version}] - {today}\n\n" + "".join(f"- {i.strip()}\n" for i in items) + "\n"
    lines = changelog.splitlines(keepends=True)
    at = next((i for i, l in enumerate(lines)
               if l.startswith("## ") and "unreleased" not in l.lower()), len(lines))
    if at == len(lines) and lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines.insert(at, entry)
    link = f"[{version}]: {TAG_URL.format(v=version)}\n"
    ref = next((i for i, l in enumerate(lines) if re.match(r"^\[\d+\.\d+\.\d+\]:", l)), None)
    if ref is None:
        lines.append(("\n" if lines and lines[-1].strip() else "") + link)
    else:
        lines.insert(ref, link)
    return "".join(lines)


def main(argv: list[str] | None = None, root: Path = ROOT) -> str:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("part", choices=["patch", "minor", "major"])
    ap.add_argument("summary", nargs="+", help="changelog bullet(s) for this version")
    args = ap.parse_args(argv)
    if not all(s.strip() for s in args.summary):
        ap.error("summary lines must not be empty")
    vfile, cfile = root / "VERSION", root / "CHANGELOG.md"
    new = bump(vfile.read_text(), args.part)
    text = cfile.read_text() if cfile.exists() else "# Changelog\n\n"
    cfile.write_text(add_entry(text, new, args.summary, date.today().isoformat()))
    vfile.write_text(new + "\n")
    print(f"VERSION -> {new}; CHANGELOG.md entry added")
    return new


if __name__ == "__main__":
    try:
        main()
    except ValueError as e:
        sys.exit(str(e))
