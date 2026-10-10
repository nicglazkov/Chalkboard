"""Output budgets: running out of room (thinking-only / max_tokens) escalates
instead of repeating the same call, wide scenes are written in parts and
assembled, and validator feedback goes back to the part it is about."""
import ast
import asyncio
import json
from types import SimpleNamespace

import pytest

from pipeline import llm, run_stats, scene_parts, telemetry
from pipeline.ast_guards import run_guards
from pipeline.retry import TimeoutExhausted


# ── fakes ─────────────────────────────────────────────────────────────────────

def _resp(text=None, stop="end_turn", out=100, thinking=True):
    content = []
    if thinking:
        content.append(SimpleNamespace(type="thinking", thinking=""))
    if text is not None:
        content.append(SimpleNamespace(type="text", text=text))
    return SimpleNamespace(content=content, stop_reason=stop, model="claude-opus-5-5",
                           usage=SimpleNamespace(input_tokens=1000, output_tokens=out, server_tool_use=None,
                                                 cache_creation_input_tokens=0, cache_read_input_tokens=0))


def _thinking_only(max_tokens):
    return _resp(None, stop="max_tokens", out=max_tokens)


class _Stream:
    def __init__(self, final):
        self.final = final

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(())

    def get_final_message(self):
        return self.final


class FakeClient:
    """messages.stream / messages.create answer through `reply(kwargs)`."""

    def __init__(self, reply, cap=128000):
        self.calls = []
        outer = self

        class _Messages:
            def stream(self, **kw):
                outer.calls.append(kw)
                return _Stream(reply(kw))

            def create(self, **kw):
                outer.calls.append(kw)
                return reply(kw)

        self.messages = _Messages()
        self.models = SimpleNamespace(retrieve=lambda model: SimpleNamespace(max_tokens=cap))


@pytest.fixture(autouse=True)
def _fresh_caps():
    llm._output_caps.clear()
    yield
    llm._output_caps.clear()


def _collect():
    events = []
    return events, telemetry.Sink(events.append)


# ── llm: detection and the budget ladder ─────────────────────────────────────

def test_thinking_only_response_is_truncation_not_a_generic_error():
    with pytest.raises(llm.ClaudeTruncated) as e:
        llm.response_text(_thinking_only(48000))
    assert e.value.thinking_only and e.value.output_tokens == 48000
    with pytest.raises(llm.ClaudeTruncated) as e:
        llm.response_text(_resp('{"a"', stop="max_tokens"))
    assert not e.value.thinking_only
    with pytest.raises(RuntimeError):  # no text for another reason stays a plain error
        llm.response_text(_resp(None, stop="end_turn"))


def test_budget_steps():
    assert llm.budget_steps(64000, 128000, "high") == [
        {"max_tokens": 64000, "effort": "high"}, {"max_tokens": 128000, "effort": "high"},
        {"max_tokens": 128000, "effort": "medium"}]
    assert llm.budget_steps(128000, 128000, "medium") == [
        {"max_tokens": 128000, "effort": "medium"}, {"max_tokens": 128000, "effort": "low"}]
    assert llm.budget_steps(200000, 64000, "low") == [{"max_tokens": 64000, "effort": "low"}]


def test_model_max_output_reads_models_api_and_falls_back():
    assert llm.model_max_output("claude-opus-5-5", FakeClient(None, cap=128000)) == 128000
    llm._output_caps.clear()
    broken = SimpleNamespace(models=SimpleNamespace(retrieve=lambda m: 1 / 0))
    assert llm.model_max_output("claude-opus-5-5", broken) == llm.DEFAULT_OUTPUT_CEILING
    assert "claude-opus-5-5" not in llm._output_caps  # a fallback is not cached


def test_big_budgets_always_stream():
    client = FakeClient(lambda kw: _resp('{"a": 1}'))
    llm.call_json("script", content="x", schema={}, max_tokens=32000, client=client)
    llm.call_json("fact", content="x", schema={}, max_tokens=16000, client=client)
    assert client.calls[0]["max_tokens"] == 32000  # went through stream()
    assert client.calls[1]["max_tokens"] == 16000


def test_out_of_room_escalates_instead_of_repeating(capsys, monkeypatch):
    monkeypatch.setenv("CLAUDE_EFFORT_MANIM", "high")
    def reply(kw):
        if kw["max_tokens"] < 128000:
            return _thinking_only(kw["max_tokens"])
        return _resp('{"ok": true}', out=70000)

    client = FakeClient(reply)
    events, sink = _collect()
    tok = telemetry.set_sink(sink)
    try:
        data, _ = asyncio.run(llm.call_json_budgeted(
            "manim", label="manim_agent", timeout=900, max_tokens=64000, content="x", schema={},
            client=client))
    finally:
        telemetry.reset_sink(tok)
    assert data == {"ok": True}
    assert [c["max_tokens"] for c in client.calls] == [64000, 128000]  # one failure, not three
    assert [c["output_config"]["effort"] for c in client.calls] == ["high", "high"]
    (b,) = [e["updates"] for e in events if e["node"] == "budget"]
    assert b["reason"] == "thinking_only" and b["from_max_tokens"] == 64000 and b["to_max_tokens"] == 128000
    usage = [e["updates"] for e in events if e["node"] == "usage"]
    assert [u["stop_reason"] for u in usage] == ["max_tokens", "end_turn"]
    assert "out of output room still thinking at max_tokens=64000" in capsys.readouterr().out


def test_every_step_out_of_room_raises_out_of_room():
    client = FakeClient(lambda kw: _resp('{"x": "unfinished', stop="max_tokens", out=kw["max_tokens"]))
    with pytest.raises(llm.ClaudeOutOfRoom) as e:
        asyncio.run(llm.call_json_budgeted("script", label="script_agent", timeout=300,
                                           max_tokens=32000, content="x", schema={}, client=client))
    assert isinstance(e.value, TimeoutExhausted)  # existing graceful handlers still apply
    assert [(c["max_tokens"], c["output_config"]["effort"]) for c in client.calls] == [
        (32000, "high"), (128000, "high"), (128000, "medium")]
    assert len(e.value.steps) == 3


def test_max_steps_one_does_not_escalate():
    client = FakeClient(lambda kw: _thinking_only(kw["max_tokens"]))
    with pytest.raises(llm.ClaudeOutOfRoom):
        asyncio.run(llm.call_json_budgeted("manim", label="m", timeout=900, max_tokens=64000,
                                           content="x", schema={}, client=client, max_steps=1))
    assert len(client.calls) == 1


def test_budget_timeout_scales_with_budget():
    assert llm.budget_timeout(300, 8000) == 300
    assert llm.budget_timeout(900, 128000) >= 128000 / llm.MIN_TOKENS_PER_SEC


# ── script agent: tighter instruction on the last step ───────────────────────

def test_script_agent_last_step_tightens(base_state):
    from pipeline.agents import script_agent as sa
    good = json.dumps({"title": "T", "script": "A.", "needs_web_search": False,
                       "segments": [{"text": "A.", "estimated_duration_sec": 1.0}]})

    def reply(kw):
        text = json.dumps(kw["messages"])
        if "LENGTH LIMIT" in text:
            return _resp(good)
        return _resp('{"title": "T", "script": "A very long', stop="max_tokens", out=kw["max_tokens"])

    client = FakeClient(reply)
    result = asyncio.run(sa.script_agent(base_state, client=client))
    assert result["script"] == "A."
    assert [c["max_tokens"] for c in client.calls] == [sa.SCRIPT_MAX_TOKENS, 128000, 128000]
    assert "LENGTH LIMIT" in json.dumps(client.calls[-1]["messages"])
    assert "LENGTH LIMIT" not in json.dumps(client.calls[0]["messages"])


# ── fact check: non-interactive runs do not dead-end ─────────────────────────

def _fact_client(verdict="needs_revision"):
    return FakeClient(lambda kw: _resp(json.dumps({"verdict": verdict, "feedback": "Fix the swing."})))


def test_fact_check_exhausted_non_interactive_continues(base_state):
    from pipeline.agents.fact_validator import fact_validator
    base_state.update(script="A script.", script_attempts=2, interactive=False)
    events, sink = _collect()
    tok = telemetry.set_sink(sink)
    try:
        out = asyncio.run(fact_validator(base_state, client=_fact_client()))
    finally:
        telemetry.reset_sink(tok)
    assert out["fact_feedback"] is None and out["script_attempts"] == 3
    (w,) = [e["updates"] for e in events if e["node"] == "warning"]
    assert w["stage"] == "fact_check" and w["feedback"] == "Fix the swing."


def test_fact_check_exhausted_interactive_still_escalates(base_state):
    from pipeline.agents.fact_validator import fact_validator
    from pipeline.graph import _after_fact_validator
    base_state.update(script="A script.", script_attempts=2, interactive=True)
    out = asyncio.run(fact_validator(base_state, client=_fact_client()))
    assert out["fact_feedback"] == "Fix the swing."
    assert _after_fact_validator({**base_state, **out}) == "escalate_to_user"


def test_fact_check_early_rejection_still_rewrites(base_state):
    from pipeline.agents.fact_validator import fact_validator
    base_state.update(script="A script.", script_attempts=0, interactive=False)
    out = asyncio.run(fact_validator(base_state, client=_fact_client()))
    assert out["fact_feedback"] == "Fix the swing." and out["script_attempts"] == 1


# ── scene parts: assembly and routing ────────────────────────────────────────

def _part_code(k, a, b):
    lines = [f"def part_{k}(self, t, _d, seg_items):"]
    for n in range(a, b + 1):
        lines.append(f"    # ── Segment {n}: Topic {n} ──")
        if n == 0:
            lines.append("    self.begin_segment(0, duration=_d[0])")
        else:
            lines.append(f"    self.next_segment({n}, duration=_d[{n}], clear=seg_items)")
        lines.append("    seg_items = []")
        if n == 0:
            lines.append('    self.title_mob = Text("Lecture 9", font_size=t.type("title"), color=t.role("body"))')
            lines.append("    reveal_with_emphasis(self, self.title_mob)")
        lines.append(f'    box = ChalkBox("{n}", role="focus_primary")')
        lines.append("    self.cue(1)")
        lines.append("    reveal_with_emphasis(self, box)")
        lines.append("    seg_items.append(box)")
    lines.append("    return seg_items")
    return "\n".join(lines)


def _parts(n=8):
    return [{"segments": [a, b], "code": _part_code(k, a, b), "imports": []}
            for k, (a, b) in enumerate(scene_parts.part_ranges(n))]


def test_part_ranges_are_balanced_and_cover_everything():
    assert scene_parts.part_ranges(8) == [(0, 2), (3, 5), (6, 7)]
    assert scene_parts.part_ranges(3) == [(0, 2)]
    assert scene_parts.part_ranges(10) == [(0, 2), (3, 5), (6, 7), (8, 9)]
    assert scene_parts.part_ranges(0) == []


def test_assembled_scene_is_one_valid_scene_that_passes_the_guards():
    parts = _parts()
    parts[1]["imports"] = ["import numpy as np", "from manim import *", "os.system('x')"]
    code, spans = scene_parts.assemble(parts, 8, "chalkboard")
    tree = ast.parse(code)
    assert run_guards(tree, code) is None
    assert code.count("import numpy as np") == 1 and "os.system" not in code
    cls = [n for n in tree.body if isinstance(n, ast.ClassDef)][0]
    assert [f.name for f in cls.body] == ["construct", "part_0", "part_1", "part_2"]
    lines = code.splitlines()
    for k, (lo, hi) in enumerate(spans):
        assert lines[lo - 1].strip() == f"def part_{k}(self, t, _d, seg_items):"
        assert lines[hi - 1].strip() == "return seg_items"
    assert 'T(theme="chalkboard")' in code and "_d = _d + [2.0] * max(0, 8 - len(_d))" in code


def test_assembled_scene_runs_its_parts_in_order():
    """Execute construct() on a stub base: every segment starts once, in order,
    and each part hands what it left on screen to the next one."""
    code, _ = scene_parts.assemble(_parts(), 8, "chalkboard")
    cls = [n for n in ast.parse(code).body if isinstance(n, ast.ClassDef)][0]
    calls = []

    class Base:
        camera = SimpleNamespace(background_color=None)
        mobjects = []

        def begin_segment(self, n, duration):
            calls.append(("begin", n))

        def next_segment(self, n, duration, clear):
            calls.append(("next", n, len(clear)))

        def cue(self, k):
            pass

        def end_layout_check(self):
            calls.append(("end",))

        def play(self, *a, **k):
            pass

    ns = {"ChalkboardSceneBase": Base, "Scene": object, "__file__": "scene.py",
          "json": SimpleNamespace(loads=lambda s: [{"actual_duration_sec": 3.0}] * 8),
          "Path": lambda *a: SimpleNamespace(parent=_Div()),
          "T": lambda theme: SimpleNamespace(bg=0, type=lambda r: 1, role=lambda r: 0),
          "Text": lambda *a, **k: object(), "ChalkBox": lambda *a, **k: object(),
          "reveal_with_emphasis": lambda scene, m: None, "FadeOut": lambda m: m}
    exec(compile(ast.Module(body=[cls], type_ignores=[]), "scene", "exec"), ns)
    ns["ChalkboardScene"]().construct()
    assert calls == [("begin", 0)] + [("next", n, 1) for n in range(1, 8)] + [("end",)]


class _Div:
    def __truediv__(self, other):
        return SimpleNamespace(read_text=lambda: "[]")


def test_route_feedback_by_line_and_segment():
    parts = _parts()
    code, spans = scene_parts.assemble(parts, 8, "chalkboard")
    line_in_part2 = spans[2][0] + 2
    assert scene_parts.route_feedback(f"Guard failed:\n1. line {line_in_part2}: self.wait(2)", parts, spans) == [2]
    assert scene_parts.route_feedback("[Segment 4 — overlap] box overlaps title", parts, spans) == [1]
    assert scene_parts.route_feedback("[Segment 0 — off_screen]\n[Segment 7 — hold_busy]", parts, spans) == [0, 2]
    assert scene_parts.route_feedback("The pacing feels rushed overall.", parts, spans) == [0, 1, 2]
    assert scene_parts.route_feedback("line 3: bad import", parts, spans) == [0, 1, 2]  # outside every part


def test_extract_method_accepts_only_the_part_method():
    good = _part_code(1, 3, 5)
    assert scene_parts.extract_method("    " + good.replace("\n", "\n    "), 1).startswith("def part_1(")
    with pytest.raises(scene_parts.PartError, match="part_1"):
        scene_parts.extract_method(good.replace("part_1", "segment_stuff"), 1)
    with pytest.raises(scene_parts.PartError, match="exactly one"):
        scene_parts.extract_method(good + "\n\ndef helper():\n    pass\n", 1)
    with pytest.raises(scene_parts.PartError, match="syntax"):
        scene_parts.extract_method("def part_1(self, t, _d, seg_items):\n    x = (", 1)
    with pytest.raises(scene_parts.PartError, match="return"):
        scene_parts.extract_method("def part_1(self, t, _d, seg_items):\n    pass", 1)


# ── manim agent: parts up front, fallback, targeted revision ────────────────

PLAN = {"title": "Lecture 9", "style": "dopants accent_cool, holes accent_warm",
        "segments": [{"index": i, "visuals": f"box {i}", "end_state": f"box {i}"} for i in range(8)]}


def _manim_reply(single_shot=None):
    def reply(kw):
        props = kw["output_config"]["format"]["schema"]["properties"]
        if "manim_code" in props:
            return single_shot(kw)
        if "style" in props:
            return _resp(json.dumps(PLAN))
        k = _part_no(kw)
        a, b = scene_parts.part_ranges(8)[k]
        return _resp(json.dumps({"imports": [], "code": _part_code(k, a, b), "edits": []}))
    return reply


def _part_no(kw):
    """The part a part call asks for (named in the last content block)."""
    return int(llm.content_text(kw["messages"][0]["content"]).split("Write part ")[1].split(":")[0])


def _wide_state(base_state, n=8, template=None):
    base_state.update(
        topic="Lecture 9", script="S.", template=template,
        script_segments=[{"text": f"Seg {i}.", "cue_text": f"[[1]] Seg {i}.", "estimated_duration_sec": 4.0}
                         for i in range(n)])
    return base_state


def test_should_chunk(base_state, monkeypatch):
    from pipeline.agents.manim_agent import should_chunk
    monkeypatch.delenv("SCENE_CHUNKING", raising=False)
    assert should_chunk(_wide_state(base_state, 8))
    assert not should_chunk(_wide_state(base_state, 4))
    assert should_chunk(_wide_state(base_state, 8, template="derivation"))
    assert not should_chunk(_wide_state(base_state, 3, template="derivation"))
    monkeypatch.setenv("SCENE_CHUNKING", "off")
    assert not should_chunk(_wide_state(base_state, 8))
    monkeypatch.setenv("SCENE_CHUNKING", "always")
    assert should_chunk(_wide_state(base_state, 2))


def test_wide_scene_is_written_in_parts(base_state, monkeypatch):
    from pipeline.agents.manim_agent import manim_agent
    monkeypatch.delenv("SCENE_CHUNKING", raising=False)
    client = FakeClient(_manim_reply())
    out = asyncio.run(manim_agent(_wide_state(base_state), client=client))
    assert len(client.calls) == 4  # plan + 3 parts
    code = out["manim_code"]
    assert run_guards(ast.parse(code), code) is None
    assert [p["segments"] for p in out["scene_parts"]] == [[0, 2], [3, 5], [6, 7]]
    assert out["scene_plan"] == PLAN
    # Every part call sees the plan and the whole script.
    for c in client.calls[1:]:
        msg = json.dumps(c["messages"])
        assert "dopants accent_cool" in msg and "Seg 7." in msg


def test_single_shot_out_of_room_falls_back_to_parts(base_state, monkeypatch, capsys):
    from pipeline.agents import manim_agent as ma
    from pipeline.agents.manim_agent import manim_agent, MANIM_MAX_TOKENS
    monkeypatch.delenv("SCENE_CHUNKING", raising=False)
    monkeypatch.setattr(ma, "should_chunk", lambda state: False)  # narrow enough for one response
    client = FakeClient(_manim_reply(single_shot=lambda kw: _thinking_only(kw["max_tokens"])))
    out = asyncio.run(manim_agent(_wide_state(base_state, template="derivation"), client=client))
    assert client.calls[0]["max_tokens"] == MANIM_MAX_TOKENS
    assert len(client.calls) == 5  # one out-of-room single shot, then plan + 3 parts
    assert out["scene_parts"] and "def part_2(" in out["manim_code"]
    assert "writing it in parts" in capsys.readouterr().out
    assert "derivation template" in json.dumps(client.calls[1]["messages"])


def test_revision_rewrites_only_the_part_the_feedback_is_about(base_state, monkeypatch):
    from pipeline.agents.manim_agent import manim_agent
    monkeypatch.delenv("SCENE_CHUNKING", raising=False)
    state = _wide_state(base_state)
    first = asyncio.run(manim_agent(state, client=FakeClient(_manim_reply())))
    state.update(first)
    state["code_feedback"] = "[Segment 4 — overlap] the box overlaps the title"
    client = FakeClient(_manim_reply())
    out = asyncio.run(manim_agent(state, client=client))
    assert len(client.calls) == 1
    msg = json.dumps(client.calls[0]["messages"])
    assert _part_no(client.calls[0]) == 1 and "Segment 4" in msg and "Current code of part 1" in msg
    assert out["scene_parts"][0] == first["scene_parts"][0] and out["scene_parts"][2] == first["scene_parts"][2]


def test_revision_of_a_replaced_scene_does_not_use_stale_parts(base_state, monkeypatch):
    from pipeline.agents.manim_agent import manim_agent
    monkeypatch.setenv("SCENE_CHUNKING", "off")
    state = _wide_state(base_state)
    state.update(scene_parts=_parts(), scene_plan=PLAN, manim_code="# a different scene",
                 code_feedback="[Segment 4 — overlap]")
    client = FakeClient(_manim_reply(single_shot=lambda kw: _resp(json.dumps({"manim_code": "# fixed"}))))
    out = asyncio.run(manim_agent(state, client=client))
    assert out["manim_code"] == "# fixed" and out["scene_parts"] is None


# ── run_stats ────────────────────────────────────────────────────────────────

def test_run_stats_record_budget_retries_and_per_agent_usage():
    ev = lambda node, ts, **u: {"node": node, "ts": ts, "updates": u}  # noqa: E731
    events = [
        ev("usage", "2026-10-09T10:00:01+00:00", agent="manim", input_tokens=10, output_tokens=64000,
           cost_usd=1.28, stop_reason="max_tokens", max_tokens=64000, effort="high"),
        ev("budget", "2026-10-09T10:00:02+00:00", agent="manim", reason="thinking_only",
           from_max_tokens=64000, to_max_tokens=128000),
        ev("usage", "2026-10-09T10:00:03+00:00", agent="manim", input_tokens=10, output_tokens=5,
           cost_usd=0.01, stop_reason="end_turn"),
        ev("warning", "2026-10-09T10:00:04+00:00", stage="fact_check", message="m", feedback="f"),
        ev("manim_agent", "2026-10-09T10:00:05+00:00"),
    ]
    stats = run_stats.build(events, started_at="2026-10-09T10:00:00+00:00",
                            finished_at="2026-10-09T10:00:06+00:00", settings={}, result="done")
    assert stats["by_agent"]["manim"] == {"calls": 2, "input_tokens": 20, "cache_read_tokens": 0,
                                          "cache_write_tokens": 0, "output_tokens": 64005,
                                          "cost_usd": 1.29, "out_of_room": 1}
    assert stats["budget_retries"][0]["to_max_tokens"] == 128000
    assert stats["warnings"][0]["stage"] == "fact_check"
    assert set(stats["stage_seconds"]) == {"manim_agent"}  # telemetry nodes are not stages


def test_split_assembled_round_trips_and_rejects_other_code():
    parts = _parts()
    parts[2]["imports"] = ["import numpy as np"]
    code, _ = scene_parts.assemble(parts, 8, "light")
    back = scene_parts.split_assembled(code, 8, "light")
    assert back is not None and scene_parts.assemble(back, 8, "light")[0] == code
    assert [p["segments"] for p in back] == [[0, 2], [3, 5], [6, 7]]
    assert scene_parts.split_assembled(code.replace("seg_items = []", "seg_items = [ ]", 1), 8, "light") is None
    assert scene_parts.split_assembled(code, 8, "chalkboard") is None  # theme line differs
    assert scene_parts.split_assembled("from manim import *\nclass X: pass\n", 8, "light") is None


def test_qa_revision_from_scene_file_revises_parts(base_state, monkeypatch):
    """Visual QA rebuilds the state from scene.py (no scene_parts): the parts are
    read back from the code and only the named part is rewritten."""
    from pipeline.agents.manim_agent import manim_agent
    monkeypatch.delenv("SCENE_CHUNKING", raising=False)
    state = _wide_state(base_state)
    code, _ = scene_parts.assemble(_parts(), 8, "chalkboard")
    state.update(manim_code=code, code_feedback="Segment 7 (part_2): dots sit below the axis")
    state.pop("scene_parts", None)
    client = FakeClient(_manim_reply())
    out = asyncio.run(manim_agent(state, client=client))
    assert len(client.calls) == 1 and _part_no(client.calls[0]) == 2
    assert "No visual plan" in json.dumps(client.calls[0]["messages"])
    assert out["scene_parts"][0]["code"] == _parts()[0]["code"]


def test_fresh_parts_after_feedback_plan_against_it(base_state, monkeypatch):
    from pipeline.agents.manim_agent import manim_agent
    monkeypatch.delenv("SCENE_CHUNKING", raising=False)
    state = _wide_state(base_state)
    state.update(manim_code="# some single-response scene", code_feedback="Title overlaps the axes")
    client = FakeClient(_manim_reply(single_shot=lambda kw: _thinking_only(kw["max_tokens"])))
    asyncio.run(manim_agent(state, client=client))
    plan_call = [c for c in client.calls if "style" in c["output_config"]["format"]["schema"]["properties"]][0]
    assert "Title overlaps the axes" in json.dumps(plan_call["messages"])
