"""Cost levers (0.7.0): cache-aware pricing, prompt caching of the scene calls,
per-role model/effort defaults, context trimming, revisions as edits, capped
code review, smaller and targeted visual QA."""
import asyncio
import json
from types import SimpleNamespace

import pytest

import config
from pipeline import llm, pricing, run_stats, scene_parts, telemetry
from tests.test_output_budget import FakeClient, PLAN, _manim_reply, _part_code, _part_no, _parts, _resp, _wide_state


# ── pricing ──────────────────────────────────────────────────────────────────

def test_cache_reads_and_writes_are_priced():
    # Opus 5.5: $4 in / $20 out, cache read $0.20, writes 1.25x (5m) or 2x (1h) input.
    assert pricing.call_cost("claude-opus-5-5", 0, 0, cache_read=1_000_000) == pytest.approx(0.20)
    assert pricing.call_cost("claude-opus-5-5", 0, 0, cache_write_5m=1_000_000) == pytest.approx(5.0)
    assert pricing.call_cost("claude-opus-5-5", 0, 0, cache_write_1h=1_000_000) == pytest.approx(8.0)
    assert pricing.call_cost("claude-sonnet-5-5", 1000, 1000, cache_read=10_000) == pytest.approx(
        1000 * 2 / 1e6 + 1000 * 10 / 1e6 + 10_000 * 0.2 / 1e6)
    assert pricing.call_cost("claude-mystery-9", 0, 0, cache_read=5) is None


def _usage(inp=100, out=10, read=0, write=0, w5=None, w1=None):
    detail = None if w5 is None and w1 is None else SimpleNamespace(
        ephemeral_5m_input_tokens=w5, ephemeral_1h_input_tokens=w1)
    return SimpleNamespace(input_tokens=inp, output_tokens=out, server_tool_use=None,
                           cache_read_input_tokens=read, cache_creation_input_tokens=write,
                           cache_creation=detail)


def test_cache_usage_split_by_ttl():
    assert llm.cache_usage(_usage(read=7, write=30, w5=10, w1=20)) == (7, 10, 20)
    # No per-TTL breakdown: counted at the dearer 1-hour rate, never understated.
    assert llm.cache_usage(_usage(read=0, write=30)) == (0, 0, 30)
    assert llm.cache_usage(SimpleNamespace()) == (0, 0, 0)


def test_usage_event_has_cache_tokens_label_role_and_a_known_cost():
    events = []
    tok = telemetry.set_sink(telemetry.Sink(events.append))
    try:
        resp = SimpleNamespace(model="claude-opus-5-5", stop_reason="end_turn",
                               usage=_usage(inp=40, out=1000, read=20_000, write=8000, w5=0, w1=8000))
        u = llm.report_usage("manim", resp, label="manim_agent part 1/3", role="manim_fix",
                             max_tokens=64000, effort="low")
    finally:
        telemetry.reset_sink(tok)
    assert u["cache_read_tokens"] == 20_000 and u["cache_write_tokens"] == 8000
    assert u["label"] == "manim_agent part 1/3" and u["role"] == "manim_fix"
    assert u["cost_usd"] == pytest.approx(40 * 4e-6 + 1000 * 20e-6 + 20_000 * 0.2e-6 + 8000 * 8e-6)
    stats = run_stats.build([{"node": "usage", "ts": "2026-10-09T10:00:01+00:00", "updates": u}],
                            started_at="2026-10-09T10:00:00+00:00",
                            finished_at="2026-10-09T10:00:02+00:00", settings={}, result="done")
    assert stats["cost_usd"] == pytest.approx(u["cost_usd"])
    assert stats["cache_read_tokens"] == 20_000 and stats["by_agent"]["manim"]["cache_write_tokens"] == 8000
    assert stats["calls"] == [{k: u[k] for k in run_stats.CALL_FIELDS if k in u}]


# ── per-role defaults ────────────────────────────────────────────────────────

def test_role_defaults_and_overrides(monkeypatch):
    for k in ("CLAUDE_MODEL_VISUAL_QA", "CLAUDE_EFFORT_MANIM", "CLAUDE_EFFORT_MANIM_PLAN",
              "CLAUDE_EFFORT_MANIM_FIX", "CLAUDE_MODEL_MANIM", "CLAUDE_MODEL_MANIM_FIX"):
        monkeypatch.delenv(k, raising=False)
    assert config.agent_model("visual_qa") == "claude-sonnet-5-5"
    assert config.agent_model("manim_plan") == config.agent_model("manim") == config.CLAUDE_MODEL
    assert config.agent_effort("manim") == "low"
    assert config.agent_effort("manim_plan") == "medium"
    assert config.agent_effort("manim_fix") == "high"
    # The parent's override reaches the sub-roles (one knob for the old behaviour) ...
    monkeypatch.setenv("CLAUDE_EFFORT_MANIM", "high")
    assert {config.agent_effort(a) for a in ("manim", "manim_plan", "manim_fix")} == {"high"}
    # ... and a sub-role's own override wins.
    monkeypatch.setenv("CLAUDE_EFFORT_MANIM_PLAN", "xhigh")
    assert config.agent_effort("manim_plan") == "xhigh"
    monkeypatch.setenv("CLAUDE_MODEL_VISUAL_QA", "claude-opus-5-5")
    assert config.agent_model("visual_qa") == "claude-opus-5-5"


def test_call_json_role_picks_model_and_effort(monkeypatch):
    monkeypatch.delenv("CLAUDE_EFFORT_MANIM", raising=False)
    monkeypatch.delenv("CLAUDE_EFFORT_MANIM_PLAN", raising=False)
    client = FakeClient(lambda kw: _resp('{"a": 1}'))
    llm.call_json("manim", content="x", schema={}, client=client, role="manim_plan")
    assert client.calls[0]["output_config"]["effort"] == "medium"


# ── prompt caching ───────────────────────────────────────────────────────────

def test_cache_system_marks_the_system_prompt(monkeypatch):
    monkeypatch.delenv("PROMPT_CACHE", raising=False)
    monkeypatch.delenv("PROMPT_CACHE_TTL", raising=False)
    client = FakeClient(lambda kw: _resp('{"a": 1}'))
    llm.call_json("manim", content="x", schema={}, client=client, system="S" * 10, cache_system=True)
    assert client.calls[0]["system"] == [{"type": "text", "text": "S" * 10,
                                          "cache_control": {"type": "ephemeral", "ttl": "1h"}}]
    monkeypatch.setenv("PROMPT_CACHE", "off")
    llm.call_json("manim", content="x", schema={}, client=client, system="S", cache_system=True)
    assert client.calls[1]["system"] == "S"
    assert "cache_control" not in llm.cached_text("t")
    monkeypatch.setenv("PROMPT_CACHE", "on")
    monkeypatch.setenv("PROMPT_CACHE_TTL", "5m")
    assert llm.cached_text("t")["cache_control"] == {"type": "ephemeral", "ttl": "5m"}


def test_part_calls_share_one_cached_prefix(base_state, monkeypatch):
    """System prompt, schema and the first two content blocks are byte-identical
    across the part calls (cached); only the last block names the part."""
    from pipeline.agents.manim_agent import manim_agent, SYSTEM_PROMPT
    monkeypatch.delenv("SCENE_CHUNKING", raising=False)
    monkeypatch.delenv("PROMPT_CACHE", raising=False)
    client = FakeClient(_manim_reply())
    asyncio.run(manim_agent(_wide_state(base_state), client=client))
    parts = client.calls[1:]
    assert len(parts) == 3
    for c in parts:
        assert c["system"][0]["text"] == SYSTEM_PROMPT and "cache_control" in c["system"][0]
        blocks = c["messages"][0]["content"]
        assert [("cache_control" in b) for b in blocks] == [True, True, False]
        assert blocks[:2] == parts[0]["messages"][0]["content"][:2]
        assert c["output_config"]["format"] == parts[0]["output_config"]["format"]
    assert sorted(_part_no(c) for c in parts) == [0, 1, 2]
    plan_call = client.calls[0]
    assert isinstance(plan_call["system"], str)  # used once: not worth a cache write


def test_staggered_starts_the_rest_after_the_first_streams():
    from pipeline.agents.manim_agent import _staggered
    order = []

    def make(k):
        async def run(on_start):
            order.append(("start", k))
            if on_start is not None:
                await asyncio.sleep(0.01)
                on_start()
                order.append(("streaming", k))
                await asyncio.sleep(0.05)
            return k
        return run

    out = asyncio.run(_staggered([make(0), make(1), make(2)]))
    assert out == [0, 1, 2]
    assert order[:2] == [("start", 0), ("streaming", 0)]
    assert {("start", 1), ("start", 2)} <= set(order[2:])


# ── context trimming ─────────────────────────────────────────────────────────

def test_plan_sees_context_only_when_asked(base_state, monkeypatch):
    from pipeline.agents.manim_agent import manim_agent
    monkeypatch.delenv("SCENE_CHUNKING", raising=False)
    ctx = [{"type": "text", "text": "LECTURE PDF TEXT"}]
    for mode, expect in (("", False), ("plan", True)):
        monkeypatch.setenv("MANIM_CONTEXT", mode)
        client = FakeClient(_manim_reply())
        asyncio.run(manim_agent(_wide_state(dict(base_state)), client=client, context_blocks=ctx))
        assert ("LECTURE PDF TEXT" in json.dumps(client.calls[0]["messages"])) is expect
        assert all("LECTURE PDF TEXT" not in json.dumps(c["messages"]) for c in client.calls[1:])


# ── revisions as edits ───────────────────────────────────────────────────────

def test_apply_edits():
    code = "def part_1(self, t, _d, seg_items):\n    x = 1\n    y = 2\n    return seg_items\n"
    assert "x = 3" in scene_parts.apply_edits(code, [{"find": "    x = 1\n", "replace": "    x = 3\n"}])
    with pytest.raises(scene_parts.PartError, match="does not occur"):
        scene_parts.apply_edits(code, [{"find": "z = 9", "replace": ""}])
    with pytest.raises(scene_parts.PartError, match="occurs 2 times"):
        scene_parts.apply_edits(code + "    x = 1\n", [{"find": "x = 1", "replace": "x = 0"}])


def _revision_state(base_state):
    state = _wide_state(base_state)
    code, _ = scene_parts.assemble(_parts(), 8, "chalkboard")
    state.update(manim_code=code, scene_parts=_parts(), scene_plan=PLAN,
                 code_feedback="[Segment 4 — overlap] box overlaps title")
    return state


def test_revision_applies_edits_to_the_part(base_state, monkeypatch):
    from pipeline.agents.manim_agent import manim_agent
    old = _parts()[1]["code"]
    line = old.splitlines()[4]
    client = FakeClient(lambda kw: _resp(json.dumps(
        {"imports": [], "code": "", "edits": [{"find": line, "replace": line + "  # fixed"}]})))
    out = asyncio.run(manim_agent(_revision_state(base_state), client=client))
    assert len(client.calls) == 1
    assert client.calls[0]["output_config"]["effort"] == config.agent_effort("manim_fix")
    assert out["scene_parts"][1]["code"] == old.replace(line, line + "  # fixed", 1)
    assert out["scene_parts"][0] == _parts()[0] and out["scene_parts"][2] == _parts()[2]


def test_edits_that_do_not_apply_get_one_reask_for_the_method(base_state):
    from pipeline.agents.manim_agent import manim_agent
    replies = iter([
        {"imports": [], "code": "", "edits": [{"find": "NOT IN THE CODE", "replace": "x"}]},
        {"imports": [], "code": _part_code(1, 3, 5).replace("Seg", "Fixed seg"), "edits": []},
    ])
    client = FakeClient(lambda kw: _resp(json.dumps(next(replies))))
    out = asyncio.run(manim_agent(_revision_state(base_state), client=client))
    assert len(client.calls) == 2
    assert "not usable" in llm.content_text(client.calls[1]["messages"][0]["content"])
    assert "Fixed seg" in out["scene_parts"][1]["code"]


# ── code review rounds ───────────────────────────────────────────────────────

def test_code_review_runs_once_by_default(base_state, monkeypatch):
    from pipeline.agents.code_validator import code_validator
    from tests.test_code_validator import VALID_CODE
    monkeypatch.delenv("CODE_REVIEW_ROUNDS", raising=False)
    client = FakeClient(lambda kw: _resp(json.dumps({"verdict": "needs_revision", "feedback": "nit"})))
    state = {**base_state, "manim_code": VALID_CODE, "code_attempts": 0}
    first = asyncio.run(code_validator(state, client=client))
    assert first["code_feedback"] == "nit" and first["claude_reviews"] == 1
    second = asyncio.run(code_validator({**state, **first}, client=client))
    assert len(client.calls) == 1 and second["code_feedback"] is None
    monkeypatch.setenv("CODE_REVIEW_ROUNDS", "0")
    third = asyncio.run(code_validator(state, client=client))
    assert len(client.calls) == 1 and third["code_feedback"] is None


# ── visual QA ────────────────────────────────────────────────────────────────

def test_qa_frames_are_scaled(monkeypatch):
    from pipeline import visual_qa
    monkeypatch.delenv("QA_FRAME_WIDTH", raising=False)
    assert visual_qa._scale_args() == ["-vf", "scale='min(1280,iw)':-2"]
    monkeypatch.setenv("QA_FRAME_WIDTH", "0")
    assert visual_qa._scale_args() == []


def test_qa_recheck_samples_only_the_changed_segments(tmp_path, monkeypatch):
    from pipeline import visual_qa
    seen = {}

    def fake_extract(video, qa_dir, ts_list):
        seen["ts"] = ts_list
        return []

    monkeypatch.setattr(visual_qa, "_extract_frames_at_timestamps", fake_extract)
    monkeypatch.setattr(visual_qa, "call_json", lambda *a, **k: ({"passed": True, "issues": []}, None))
    segs = [{"text": f"s{i}", "actual_duration_sec": 10.0} for i in range(8)]
    visual_qa.visual_qa(tmp_path / "v.mp4", tmp_path, segments=segs, only_segments={3, 4})
    # The frames a full pass samples (capped at 10 of 17), restricted to segments 3 and 4.
    assert [(t, s) for t, s, _ in seen["ts"]] == [(35.0, 3), (39.2, 3), (49.2, 4)]


def test_changed_segments_after_a_qa_fix():
    import main
    old, _ = scene_parts.assemble(_parts(), 8, "chalkboard")
    new_parts = _parts()
    new_parts[2] = {**new_parts[2], "code": new_parts[2]["code"].replace("Seg", "New seg")}
    assert main._changed_segments(old, {"scene_parts": new_parts}, 8, "chalkboard") == {6, 7}
    assert main._changed_segments("# single-shot scene", {"scene_parts": new_parts}, 8, "chalkboard") is None


def test_qa_fix_shows_the_fixer_its_segments_frames(base_state, tmp_path):
    from pipeline.agents.manim_agent import manim_agent
    frames = []
    for s in range(8):
        p = tmp_path / f"frame_{s:02d}.png"
        p.write_bytes(b"\x89PNG fake " + bytes([s]))
        frames.append({"path": str(p), "segment": s, "t": 10.0 * s})
    state = _revision_state(base_state)
    state["qa_frames"] = frames
    line = _parts()[1]["code"].splitlines()[4]
    client = FakeClient(lambda kw: _resp(json.dumps(
        {"imports": [], "code": "", "edits": [{"find": line, "replace": line}]})))
    asyncio.run(manim_agent(state, client=client))
    blocks = client.calls[0]["messages"][0]["content"]
    images = [b for b in blocks if b["type"] == "image"]
    assert len(images) == 3  # segments 3, 4 and 5 of part 1
    labels = [b["text"] for b in blocks if b["type"] == "text" and b["text"].startswith("QA frame")]
    assert labels == ["QA frame of segment 3 at t=30.0s", "QA frame of segment 4 at t=40.0s",
                      "QA frame of segment 5 at t=50.0s"]
    assert "cache_control" in blocks[1] and blocks[-1]["text"].startswith("Write part 1")


def test_script_context_is_cached_for_rewrites(monkeypatch):
    from pipeline.agents.script_agent import script_agent
    monkeypatch.delenv("PROMPT_CACHE", raising=False)
    reply = {"title": "T", "script": "Hi.", "segments": [{"text": "Hi.", "cue_text": "Hi.",
                                                          "estimated_duration_sec": 1.0}],
             "needs_web_search": False}
    client = FakeClient(lambda kw: _resp(json.dumps(reply)))
    ctx = [{"type": "text", "text": "--- file: a.txt ---"}, {"type": "text", "text": "notes"}]
    state = {"topic": "t", "effort_level": "medium", "audience": "beginner", "tone": "casual"}
    asyncio.run(script_agent(state, client=client, context_blocks=ctx))
    blocks = client.calls[0]["messages"][0]["content"]
    marked = [b for b in blocks if "cache_control" in b]
    assert marked == [{"type": "text", "text": "notes", "cache_control": {"type": "ephemeral", "ttl": "5m"}}]
    assert "cache_control" not in ctx[-1]  # the caller's blocks are not modified
