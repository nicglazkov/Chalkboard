import json

import pytest

from pipeline import pricing, run_stats


def test_prices_from_table():
    assert pricing.call_cost("claude-opus-5-5", 1_000_000, 1_000_000) == pytest.approx(24.0)
    assert pricing.call_cost("claude-sonnet-5-5", 1_000_000, 0) == pytest.approx(2.0)
    assert pricing.call_cost("claude-haiku-4-5", 0, 1_000_000) == pytest.approx(5.0)
    assert pricing.call_cost("claude-haiku-4-5-20251001", 0, 1_000_000) == pytest.approx(5.0)
    # $10 per 1000 searches
    assert pricing.call_cost("claude-opus-5-5", 0, 0, web_searches=3) == pytest.approx(0.03)


def test_unknown_inputs_give_none():
    assert pricing.call_cost("claude-mystery-9", 10, 10) is None
    assert pricing.call_cost(None, 10, 10) is None
    assert pricing.call_cost("claude-opus-5-5", None, 10) is None
    assert pricing.call_cost("claude-opus-5-5", 10, 10, web_searches=None) is None
    assert pricing.call_cost("claude-opus-5-5", 10, 10, cache_read=None) is None
    assert pricing.price_for("claude-opus-5-5-beta") is None


def _ev(node, ts, **updates):
    return {"node": node, "updates": updates, "ts": ts}


EVENTS = [
    _ev("usage", "2026-10-02T10:00:05+00:00", agent="script", model="claude-opus-5-5",
        input_tokens=1000, output_tokens=500, web_searches=0, cost_usd=0.014),
    _ev("peek", "2026-10-02T10:00:06+00:00", stage="script", text="x", done=True),
    _ev("init", "2026-10-02T10:00:01+00:00"),
    _ev("script_agent", "2026-10-02T10:00:10+00:00"),
    _ev("fact_validator", "2026-10-02T10:00:15+00:00"),
    _ev("tts", "2026-10-02T10:00:20+00:00", status="done", segments_done=3, segments=3, chars=420),
    _ev("render_trigger", "2026-10-02T10:00:21+00:00"),
    _ev("render", "2026-10-02T10:00:22+00:00", status="running"),
    _ev("render", "2026-10-02T10:00:30+00:00", status="running", segment=1, segments=3),
    _ev("render", "2026-10-02T10:01:22+00:00", status="done"),
]


def test_totals_and_stage_seconds():
    events = sorted(EVENTS, key=lambda e: e["ts"])
    t = run_stats.totals(events)
    assert t == {"calls": 1, "input_tokens": 1000, "output_tokens": 500, "web_searches": 0,
                 "cache_read_tokens": 0, "cache_write_tokens": 0,
                 "cost_usd": 0.014, "tts_chars": 420}
    st = run_stats.stage_seconds(events, "2026-10-02T10:00:00+00:00")
    assert st == {"init": 1.0, "script_agent": 9.0, "fact_validator": 5.0,
                  "render_trigger": 6.0, "render": 60.0}


def test_cost_unknown_if_any_call_unknown_and_no_tts_is_null():
    events = [_ev("usage", "2026-10-02T10:00:05+00:00", input_tokens=1, output_tokens=1,
                  web_searches=0, cost_usd=None),
              _ev("usage", "2026-10-02T10:00:06+00:00", input_tokens=1, output_tokens=1,
                  web_searches=0, cost_usd=0.5)]
    t = run_stats.totals(events)
    assert t["cost_usd"] is None
    assert t["tts_chars"] is None
    assert t["input_tokens"] == 2
    assert run_stats.totals([])["cost_usd"] == 0  # no calls made: nothing spent


def test_build_and_write(tmp_path):
    events = sorted(EVENTS, key=lambda e: e["ts"])
    stats = run_stats.build(events, started_at="2026-10-02T10:00:00+00:00",
                            finished_at="2026-10-02T10:01:30+00:00",
                            settings={"run_id": "r1", "effort": "low", "quality": "low", "narrator": "kokoro",
                                      "source": "server"},
                            result="done")
    assert stats["total_seconds"] == 90.0
    assert stats["research"] is False
    assert stats["resumed"] is False
    assert run_stats.write(tmp_path, stats) == tmp_path / "run_stats.json"
    assert run_stats.read(tmp_path)["cost_usd"] == 0.014
    resumed = run_stats.build(events, started_at="2026-10-02T10:00:00+00:00",
                              finished_at="2026-10-02T10:01:30+00:00", settings={}, result="done",
                              resumed=True)
    assert resumed["research"] is None
    # A resume must not erase the original record.
    run_stats.write(tmp_path, resumed)
    rec = run_stats.read(tmp_path)
    assert rec["resumed"] is False and rec["total_seconds"] == 90.0
    assert rec["later_invocations"][0]["resumed"] is True


def test_recorder_records_graph_events():
    r = run_stats.Recorder()
    r.record_graph({"script_agent": {"status": "validating"}, "__end__": {}})
    r({"node": "usage", "updates": {"input_tokens": 1}, "ts": "2026-10-02T10:00:00+00:00"})
    assert [e["node"] for e in r.events] == ["script_agent", "usage"]
    assert r.events[1]["ts"] == "2026-10-02T10:00:00+00:00"
    assert r.events[0]["ts"]
