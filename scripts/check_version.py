#!/usr/bin/env python3
"""CI gate: VERSION must be valid semver, strictly greater than the base
branch's VERSION, and have a `## [X.Y.Z]` heading in CHANGELOG.md.

    python scripts/check_version.py <base-version-or-empty>
"""
from __future__ import annotations
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
HOW = ("Bump it with:  python scripts/bump_version.py patch \"what changed\"\n"
       "(minor for a notable feature, major for a breaking change). That updates VERSION\n"
       "and adds the CHANGELOG.md entry; commit both. If main moved on, merge main first\n"
       "and bump past its VERSION.")


def parse(v: str) -> tuple[int, int, int] | None:
    m = SEMVER.match(v.strip())
    return tuple(int(x) for x in m.groups()) if m else None


def check(head: str, base: str | None, changelog: str) -> list[str]:
    errors = []
    h = parse(head)
    if h is None:
        return [f"VERSION is {head.strip()!r}, not MAJOR.MINOR.PATCH (e.g. 0.3.1)."]
    b = parse(base) if base and base.strip() else (0, 0, 0)
    if b is None:
        b = (0, 0, 0)
    if h <= b:
        errors.append(f"VERSION {head.strip()} is not greater than the base branch's {base.strip() if base else '(none)'}. "
                      "Every change merged to main bumps the version.")
    if not re.search(rf"^##\s+\[?{re.escape(head.strip())}\]?(\s|$)", changelog, re.M):
        errors.append(f"CHANGELOG.md has no '## [{head.strip()}] - YYYY-MM-DD' entry.")
    return errors


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else ""
    head = (ROOT / "VERSION").read_text() if (ROOT / "VERSION").exists() else ""
    changelog = (ROOT / "CHANGELOG.md").read_text() if (ROOT / "CHANGELOG.md").exists() else ""
    errors = check(head, base, changelog)
    if errors:
        print("Version check failed:\n  - " + "\n  - ".join(errors) + "\n\n" + HOW)
        return 1
    print(f"VERSION {head.strip()} > base {base.strip() or '(none)'}; CHANGELOG entry present.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
