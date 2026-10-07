"""Versioning: VERSION file, git facts, /version endpoints, CLI flag, run records, bump + CI check scripts."""
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from pipeline import version as ver

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import bump_version  # noqa: E402
import check_version  # noqa: E402


def _client():
    from server.app import create_app
    from server.jobs import JobStore
    lib = MagicMock(); lib.init = AsyncMock(); lib.get_video = AsyncMock(return_value=None)
    return TestClient(create_app(store=JobStore(), library_store=lib))


# ── version module ──────────────────────────────────────────────────────

def test_version_file_is_semver_and_in_changelog():
    v = (ROOT / "VERSION").read_text().strip()
    assert ver.SEMVER_RE.match(v)
    assert ver.__version__ == v
    assert ver.changelog_has(v)


def test_read_version(tmp_path):
    f = tmp_path / "VERSION"
    assert ver.read_version(f) is None            # missing
    f.write_text("1.2.3\n")
    assert ver.read_version(f) == "1.2.3"
    f.write_text("not-a-version")
    assert ver.read_version(f) is None


def test_git_fields_null_outside_a_repo(tmp_path):
    info = ver.git_info(tmp_path)
    assert info == {"commit": None, "short": None, "date": None, "branch": None, "dirty": None}


def test_git_fields_null_without_git(tmp_path, monkeypatch):
    monkeypatch.setattr(ver.shutil, "which", lambda _: None)
    assert all(v is None for v in ver.git_info(ROOT).values())


def test_git_fields_from_a_real_repo(tmp_path):
    if ver.shutil.which("git") is None:
        pytest.skip("git not installed")
    run = lambda *a: subprocess.run(["git", "-C", str(tmp_path), *a], check=True, capture_output=True)
    run("init", "-q", "-b", "main")
    (tmp_path / "f.txt").write_text("a")
    run("add", "f.txt")
    run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "x")
    info = ver.git_info(tmp_path)
    assert len(info["commit"]) == 40 and info["commit"].startswith(info["short"])
    assert info["branch"] == "main" and info["dirty"] is False and info["date"]
    (tmp_path / "f.txt").write_text("b")
    assert ver.git_info(tmp_path)["dirty"] is True
    # A folder nested inside a repo is not that repo's checkout.
    (tmp_path / "sub").mkdir()
    assert ver.git_info(tmp_path / "sub")["commit"] is None


def test_latest_changelog(tmp_path):
    f = tmp_path / "CHANGELOG.md"
    f.write_text("# Changelog\n\n## [Unreleased]\n\n- wip\n\n## [1.2.0] - 2026-01-02\n\n- one\n- two\n  more\n\n"
                 "## [1.1.0] - 2026-01-01\n\n- old\n")
    e = ver.latest_changelog(f)
    assert e == {"version": "1.2.0", "date": "2026-01-02", "heading": "[1.2.0] - 2026-01-02", "items": ["one", "two"]}
    assert ver.latest_changelog(tmp_path / "nope.md") is None


# ── endpoints ───────────────────────────────────────────────────────────

def test_version_endpoints_shape_and_no_store():
    c = _client()
    pretty = c.get("/version")
    compact = c.get("/api/version")
    for r in (pretty, compact):
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-store"
        assert r.headers["content-type"].startswith("application/json")
    assert "\n  " in pretty.text                      # pretty-printed for browsers
    d = compact.json()
    assert d["name"] == "chalkboard" and d["version"] == ver.__version__
    assert set(d["git"]) == {"commit", "short", "date", "branch", "dirty"}
    assert {"started_at", "uptime_s", "host", "port"} <= set(d["server"])
    assert d["changelog"]["version"] == ver.__version__
    assert set(d["runtime"]) == {"python", "manim", "ffmpeg"}
    assert {"model", "render_backend", "narrator", "pace"} <= set(d["config"])
    paths = {e["path"] for e in d["endpoints"]}
    assert {"/api/meta", "/api/status", "/api/library", "/api/jobs", "/openapi.json", "/docs"} <= paths
    assert json.loads(pretty.text)["version"] == d["version"]


def test_version_has_no_secrets(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-xyz")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-secret-xyz")
    text = _client().get("/version").text
    assert "secret-xyz" not in text and "API_KEY" not in text


def test_meta_and_status_include_version(monkeypatch):
    from server import status as status_mod
    monkeypatch.setattr(status_mod, "status", AsyncMock(return_value={"overall": "ok", "checks": []}))
    c = _client()
    assert c.get("/api/meta").json()["version"] == ver.__version__
    s = c.get("/api/status").json()
    assert s["version"] == ver.__version__ and s["overall"] == "ok"
    assert c.get("/openapi.json").json()["info"]["version"] == ver.__version__


# ── CLI + run records ───────────────────────────────────────────────────

def test_cli_version_flag(monkeypatch, capsys):
    import main
    monkeypatch.setattr(sys, "argv", ["main.py", "--version"])
    with pytest.raises(SystemExit) as e:
        main.main()
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith(f"chalkboard {ver.__version__}")


def test_run_stats_records_version():
    from pipeline import run_stats
    s = run_stats.build([], started_at="2026-10-02T10:00:00+00:00", finished_at="2026-10-02T10:00:01+00:00",
                        settings={"run_id": "r"}, result="done")
    assert s["chalkboard_version"] == ver.__version__
    assert s["git_commit"] == ver.git()["commit"]


# ── bump + CI check scripts ─────────────────────────────────────────────

def test_bump():
    assert bump_version.bump("0.3.1\n", "patch") == "0.3.2"
    assert bump_version.bump("0.3.1", "minor") == "0.4.0"
    assert bump_version.bump("0.3.1", "major") == "1.0.0"
    with pytest.raises(ValueError):
        bump_version.bump("v1", "patch")


def test_bump_script_writes_files(tmp_path, capsys):
    (tmp_path / "VERSION").write_text("0.3.1\n")
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\nIntro.\n\n## [0.3.1] - 2026-10-06\n\n- old\n\n"
                                           "[0.3.1]: https://example/v0.3.1\n")
    assert bump_version.main(["minor", "Add pacing", "Second"], root=tmp_path) == "0.4.0"
    assert (tmp_path / "VERSION").read_text() == "0.4.0\n"
    text = (tmp_path / "CHANGELOG.md").read_text()
    assert text.index("## [0.4.0] - ") < text.index("## [0.3.1]") < text.index("[0.4.0]: ") < text.index("[0.3.1]: ")
    assert "- Add pacing\n- Second\n" in text
    assert ver.latest_changelog(tmp_path / "CHANGELOG.md")["items"] == ["Add pacing", "Second"]
    assert check_version.check("0.4.0", "0.3.1", text) == []


def test_check_version():
    cl = "## [0.3.2] - 2026-10-07\n\n- x\n"
    assert check_version.check("0.3.2", "0.3.1", cl) == []
    assert check_version.check("0.3.2", "", cl) == []                    # base has no VERSION yet
    assert check_version.check("0.3.1", "0.3.1", "## [0.3.1]\n")       # not bumped
    assert check_version.check("0.3.0", "0.3.1", "## [0.3.0]\n")       # went backwards
    assert check_version.check("0.10.0", "0.9.9", "## [0.10.0]\n") == []  # numeric, not string, compare
    assert check_version.check("0.3.2", "0.3.1", "## [0.3.1]\n")       # no changelog entry
    assert check_version.check("0.3.2-rc1", "0.3.1", cl)               # not plain semver
