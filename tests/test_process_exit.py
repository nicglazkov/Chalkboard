# tests/test_process_exit.py
"""main.py must end promptly once a run's files are written, survive a reader
that went away, and share the checkpoint DB with concurrent runs.

Context (2026-10-09): two runs started at once through the remote client;
one client never returned after its video was done (244514b9, 9043e20b,
3253240e). These tests pin the process-side guarantees."""
import asyncio
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _run_script(code: str, timeout: float, **kw):
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code)], cwd=REPO,
                          capture_output=True, text=True, timeout=timeout, **kw)


_FAKE_RUN = textwrap.dedent("""
    import sys, threading, time
    import main
    def fake_main():
        # A worker stuck in a blocking call, like an asyncio.to_thread HTTP
        # request whose wait_for timed out: non-daemon, outlives the run.
        threading.Thread(target=time.sleep, args=(600,), name="stuck-call").start()
        print("Done -> output/fake/final.mp4", flush=True)
    main.main = fake_main
    main._guard_stdio()
    code = main._run_cli()
""")


def test_process_exits_promptly_despite_a_stuck_worker_thread():
    t0 = time.monotonic()
    r = _run_script(_FAKE_RUN + "main._exit_promptly(code, grace=1.0)\nsys.exit(code)\n", timeout=60)
    elapsed = time.monotonic() - t0
    assert r.returncode == 0, r.stderr
    assert "Done ->" in r.stdout
    assert "[exit] 1 worker thread(s) still running" in r.stderr
    assert elapsed < 30


def test_without_the_exit_guard_the_same_run_hangs():
    """Control: a plain interpreter exit waits for the stuck thread."""
    with pytest.raises(subprocess.TimeoutExpired):
        _run_script(_FAKE_RUN + "sys.exit(code)\n", timeout=8)


def test_no_stuck_threads_means_a_normal_exit():
    r = _run_script("""
        import main, sys
        main.main = lambda: print("ok")
        main._guard_stdio()
        code = main._run_cli()
        main._exit_promptly(code, grace=1.0)
        print("after exit guard")
        sys.exit(code)
    """, timeout=60)
    assert r.returncode == 0 and "after exit guard" in r.stdout


@pytest.mark.parametrize("body,code", [
    ("raise SystemExit('Aborted.')", 1),
    ("raise SystemExit(3)", 3),
    ("raise SystemExit(None)", 0),
    ("raise RuntimeError('boom')", 1),
])
def test_run_cli_exit_codes(body, code, monkeypatch, capsys):
    import main

    def fake():
        exec(body)

    monkeypatch.setattr(main, "main", fake)
    assert main._run_cli() == code


def test_output_to_a_closed_pipe_does_not_fail_the_run(tmp_path):
    """Run 244514b9: the client's pipe closed and the next print raised
    BrokenPipeError, so a finished re-render was recorded as failed."""
    r_fd, w_fd = os.pipe()
    os.close(r_fd)                       # the reader is gone
    script = textwrap.dedent("""
        import sys, main
        def fake_main():
            for i in range(200):
                print("progress line", i, flush=True)
            open(sys.argv[1], "w").write("finished")
        main.main = fake_main
        main._guard_stdio()
        code = main._run_cli()
        main._exit_promptly(code, grace=1.0)
        sys.exit(code)
    """)
    marker = tmp_path / "marker"
    try:
        p = subprocess.run([sys.executable, "-c", script, str(marker)], cwd=REPO,
                           stdout=w_fd, stderr=w_fd, timeout=60)
    finally:
        os.close(w_fd)
    assert p.returncode == 0
    assert marker.read_text() == "finished"


def test_concurrent_runs_share_the_checkpoint_db(tmp_path):
    """Two runs checkpointing into one SQLite file at the same time (separate
    connections, as two CLI processes or server jobs have) both complete."""
    import main
    from typing import TypedDict
    from langgraph.graph import END, StateGraph

    class S(TypedDict):
        n: int

    def step(state: S) -> S:
        return {"n": state["n"] + 1}

    def build(checkpointer):
        g = StateGraph(S)
        for i in range(12):
            g.add_node(f"s{i}", step)
        g.set_entry_point("s0")
        for i in range(11):
            g.add_edge(f"s{i}", f"s{i + 1}")
        g.add_edge("s11", END)
        return g.compile(checkpointer=checkpointer)

    db = str(tmp_path / "state.db")

    async def one(tid: str) -> int:
        async with main._open_checkpointer(db) as cp:
            graph = build(cp)
            cfg = {"configurable": {"thread_id": tid}}
            out = await graph.ainvoke({"n": 0}, config=cfg)
            snap = await graph.aget_state(cfg)
            assert not snap.next
            return out["n"]

    async def both():
        return await asyncio.gather(*(one(f"run-{k}") for k in range(4)))

    assert asyncio.run(both()) == [12, 12, 12, 12]
    import sqlite3
    con = sqlite3.connect(db)
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    threads = {r[0] for r in con.execute("select distinct thread_id from checkpoints")}
    assert threads == {f"run-{k}" for k in range(4)}
