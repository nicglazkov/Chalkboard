# tests/test_chalkboard_circuits.py
"""Circuit schematic symbols (docker/chalkboard_circuits.py).

Geometry and wiring contracts: terminals sit where the leads end and follow
transforms, labels stay off the wires, the standard arrangements connect
the right terminals, and highlight / mute reach every stroke. Gated on
Manim like the other component tests.
"""
import numpy as np
import pytest

manim = pytest.importorskip("manim")

from manim import LEFT, RIGHT, UP, UR  # noqa: E402
from manim.utils.color import ManimColor  # noqa: E402

from docker.chalkboard_circuits import (  # noqa: E402
    Capacitor,
    CircuitGroup,
    CmosInverter,
    CurrentArrow,
    Ground,
    InverterChain,
    InverterGate,
    Junction,
    Mosfet,
    NMOS,
    PMOS,
    PassTransistor,
    Resistor,
    ResistorLoadInverter,
    Supply,
    Switch,
    VSource,
    Wire,
)
from docker.chalkboard_components import _TEXTUAL  # noqa: E402
from pipeline.design_tokens import T  # noqa: E402


def _close(p, q, tol=1e-6):
    return np.allclose(np.asarray(p)[:2], np.asarray(q)[:2], atol=tol)


def _box(m):
    pts = m.get_all_points()
    return pts.min(axis=0), pts.max(axis=0)


def _boxes_overlap(a, b, pad=0.0):
    (alo, ahi), (blo, bhi) = _box(a), _box(b)
    return not (ahi[0] + pad < blo[0] or bhi[0] + pad < alo[0]
                or ahi[1] + pad < blo[1] or bhi[1] + pad < alo[1])


# ── Two-terminal elements ────────────────────────────────────────────


@pytest.mark.parametrize("cls", [Resistor, Capacitor, VSource, Switch])
@pytest.mark.parametrize("a,b", [([0, 0], [3, 0]), ([0, 2], [0, -1]), ([0, 0], [2, 2])])
def test_two_terminal_terminals_are_the_endpoints(cls, a, b):
    el = cls(a, b)
    assert _close(el.terminal("a"), a)
    assert _close(el.terminal("b"), b)
    assert set(el.terminals) == {"a", "b"}


def test_resistor_is_a_zigzag_centred_on_the_segment():
    r = Resistor([0, 0], [3, 0])
    pts = r.body.points
    ys = pts[:, 1]
    assert ys.max() > 0.1 and ys.min() < -0.1, "zigzag must swing both sides of the wire"
    assert abs((pts[:, 0].min() + pts[:, 0].max()) / 2 - 1.5) < 1e-6
    # the leads run along the wire, so the whole element spans a to b
    lo, hi = _box(r)
    assert _close([lo[0], 0], [0, 0]) and _close([hi[0], 0], [3, 0])


def test_capacitor_plates_are_perpendicular_to_the_axis():
    c = Capacitor([0, 1], [0, -1])
    for plate in c.plates:
        s, e = plate.get_start(), plate.get_end()
        assert abs(s[1] - e[1]) < 1e-6 and abs(s[0] - e[0]) > 0.5  # horizontal plates
    gap = abs(c.plates[0].get_start()[1] - c.plates[1].get_start()[1])
    assert 0.1 < gap < 0.3


def test_vsource_plus_sits_toward_the_positive_terminal():
    src = VSource([0, -1.5], [0, 1.5])
    texts = [m for m in src.body.submobjects if isinstance(m, _TEXTUAL)]
    plus, minus = texts  # built in this order
    assert plus.get_center()[1] > minus.get_center()[1]


@pytest.mark.parametrize("a,b,direction", [([0, 0], [3, 0], UP), ([0, 1.5], [0, -1.5], RIGHT)])
def test_label_sits_beside_the_body_off_the_wire(a, b, direction):
    r = Resistor(a, b, r"R_1 = 2\,\mathrm{k\Omega}")
    assert r.label is not None
    for part in (*r.leads, r.body):
        assert not _boxes_overlap(r.label, part), "label must not touch a wire or the zigzag"
    offset = r.label.get_center() - r.body.get_center()
    assert np.dot(offset, direction) > 0


def test_label_dir_overrides_the_side():
    r = Resistor([0, 1.5], [0, -1.5], "R", label_dir=LEFT)
    assert r.label.get_center()[0] < 0


def test_terminals_follow_transforms():
    r = Resistor([0, 0], [2, 0])
    r.scale(0.5, about_point=np.zeros(3)).shift(UP * 1.0)
    assert _close(r.terminal("a"), [0, 1])
    assert _close(r.terminal("b"), [1, 1])
    moved = r.copy().shift(RIGHT)
    assert _close(moved.terminal("a"), [1, 1])


def test_unknown_terminal_names_the_valid_ones():
    with pytest.raises(KeyError, match="'a', 'b'"):
        Resistor([0, 0], [1, 0]).terminal("x")


def test_coincident_endpoints_raise():
    with pytest.raises(ValueError):
        Resistor([1, 1], [1, 1])


def test_switch_set_closed_moves_the_blade_onto_the_far_contact():
    sw = Switch([0, 0], [2, 0])
    assert not sw.closed
    far = sw.contacts[1].get_center()
    assert not _close(sw.blade.get_end(), far, tol=1e-3)
    sw.set_closed(True)
    assert sw.closed and _close(sw.blade.get_end(), far, tol=1e-6)
    sw.set_closed(False)
    assert not _close(sw.blade.get_end(), far, tol=1e-3)


# ── Wires, nodes, references ─────────────────────────────────────────


def test_wire_routes_with_one_square_corner():
    w = Wire([0, 0], [2, 1], route="hv")
    corners = w.path.get_anchors()
    assert any(_close(p, [2, 0]) for p in corners)
    assert _close(w.terminal("start"), [0, 0]) and _close(w.terminal("end"), [2, 1])
    v = Wire([0, 0], [2, 1], route="vh")
    assert any(_close(p, [0, 1]) for p in v.path.get_anchors())


def test_wire_dots_and_bad_routes():
    w = Wire([0, 0], [2, 0], dots=[[1, 0]])
    assert len(w.dots) == 1 and _close(w.dots[0].get_center(), [1, 0])
    with pytest.raises(ValueError):
        Wire([0, 0])
    with pytest.raises(ValueError):
        Wire([0, 0], [1, 0], [2, 1], route="hv")


def test_junction_open_and_label():
    j = Junction([1, 1], r"V_{\mathrm{out}}", direction=UR)
    assert _close(j.terminal("p"), [1, 1])
    assert j.label.get_center()[0] > 1 and j.label.get_center()[1] > 1
    port = Junction([0, 0], open=True)
    assert port.dot.get_fill_opacity() == 0


def test_ground_and_supply_hang_from_their_point():
    g = Ground([0, 0])
    lo, hi = _box(g)
    assert abs(hi[1]) < 1e-6 and lo[1] < -0.3
    s = Supply([0, 0])
    lo, hi = _box(s)
    assert abs(lo[1]) < 1e-6 and s.label.get_center()[1] > 0.2


def test_current_arrow_is_parallel_and_offset():
    ca = CurrentArrow([0, 0], [3, 0], "I")
    start, end = ca.arrow.get_start(), ca.arrow.get_end()
    assert end[0] > start[0] and abs(start[1] - end[1]) < 1e-6
    assert start[1] > 0  # above a horizontal wire by default
    below = CurrentArrow([0, 0], [3, 0], side="right")
    assert below.arrow.get_start()[1] < 0


# ── Transistors ──────────────────────────────────────────────────────


def test_nmos_drain_on_top_pmos_source_on_top():
    n = NMOS([0, 0])
    assert n.terminal("d")[1] > n.terminal("s")[1]
    assert n.terminal("g")[0] < 0 and n.bubble is None
    p = PMOS([0, 0])
    assert p.terminal("s")[1] > p.terminal("d")[1]
    assert p.bubble is not None


def test_mosfet_flip_mirror_body_and_angle():
    assert NMOS([0, 0], flip=True).terminal("s")[1] > 0
    assert NMOS([0, 0], mirror=True).terminal("g")[0] > 0
    nb = NMOS([0, 0], body=True)
    assert set(nb.terminals) == {"g", "d", "s", "b"} and nb.terminal("b")[0] > 0
    h = NMOS([0, 0], angle=-90)
    assert h.terminal("g")[1] > 0, "angle=-90 puts the gate on top"
    assert h.terminal("d")[0] > 0 > h.terminal("s")[0]


def test_mosfet_terminal_labels_stay_off_the_leads():
    n = NMOS([0, 0], terminal_labels=True)
    assert len(n.terminal_labels) == 3
    for lab in n.terminal_labels:
        for lead in (n.drain, n.source, n.gate_lead):
            assert not _boxes_overlap(lab, lead), "terminal label touches a lead"


def test_mosfet_rejects_unknown_kind():
    with pytest.raises(ValueError):
        Mosfet([0, 0], "x")


# ── Arrangements ─────────────────────────────────────────────────────


def test_cmos_inverter_topology():
    inv = CmosInverter([0, 0], load=True)
    p, n = inv.pmos.terminals, inv.nmos.terminals
    assert _close(inv.terminal("vdd"), p["s"])      # PMOS source on VDD
    assert _close(inv.terminal("gnd"), n["s"])      # NMOS source on ground
    out_node = inv.out_dot.terminal("p")
    assert abs(out_node[0] - p["d"][0]) < 1e-6     # both drains meet at Out
    assert n["d"][1] < out_node[1] < p["d"][1]
    assert inv.terminal("in")[0] < p["g"][0] and inv.terminal("out")[0] > out_node[0]
    assert inv.cap is not None and inv.cap.terminal("b")[1] < out_node[1]
    assert CmosInverter([0, 0]).cap is None


def test_cmos_paths_highlight_only_their_transistor():
    t = T()
    inv = CmosInverter([0, 0])
    inv.pull_down.highlight("accent_warm")
    warm = ManimColor(t.role("accent_warm")).to_hex().lower()
    assert inv.nmos.channel.get_stroke_color().to_hex().lower() == warm
    assert inv.pmos.channel.get_stroke_color().to_hex().lower() != warm


def test_resistor_load_inverter_topology():
    rl = ResistorLoadInverter([0, 0], load=True)
    assert _close(rl.resistor.terminal("a"), rl.terminal("vdd"))
    assert rl.resistor.terminal("b")[1] > rl.nmos.terminal("d")[1]
    assert _close(rl.terminal("gnd"), rl.nmos.terminal("s"))
    assert rl.cap is not None


def test_pass_transistor_layout():
    pt = PassTransistor([0, 0], "n")
    assert pt.terminal("in")[0] < pt.fet.terminal("s")[0] < pt.fet.terminal("d")[0] < pt.terminal("out")[0]
    assert pt.terminal("gate")[1] > 0
    assert _close(pt.cap.terminal("a"), pt.terminal("out"))
    pp = PassTransistor([0, 0], "p", load_label=None)
    assert pp.cap is None and pp.fet.bubble is not None


def test_inverter_gate_and_chain():
    g = InverterGate([0, 0], "4")
    assert g.terminal("in")[0] < 0 < g.terminal("out")[0]
    assert g.label.get_center()[1] > 0
    ch = InverterChain(["1", "4", "16"])
    assert len(ch.gates) == 3
    assert {"in", "out", "n0", "n1", "n2"} <= set(ch.terminals)
    xs = [gate.get_center()[0] for gate in ch.gates]
    assert xs == sorted(xs)
    assert ch.cap is not None and InverterChain(["1"], load_label=None).cap is None


# ── Emphasis and layout-check contract ───────────────────────────────


def test_highlight_and_mute_reach_strokes_dots_and_labels():
    t = T()
    w = Wire([0, 0], [2, 0], dots=[[1, 0]])
    w.highlight("focus_primary")
    focus = ManimColor(t.role("focus_primary")).to_hex().lower()
    assert w.path.get_stroke_color().to_hex().lower() == focus
    assert w.dots[0].get_fill_color().to_hex().lower() == focus
    assert w.path.get_stroke_width() == t.stroke_width("bold")
    r = Resistor([0, 0], [2, 0], "R")
    r.mute()
    muted = ManimColor(t.role("context_muted")).to_hex().lower()
    glyph = r.label.family_members_with_points()[0]
    assert glyph.get_fill_color().to_hex().lower() == muted
    assert r.body.get_stroke_opacity() < 1.0
    r.highlight()
    assert r.body.get_stroke_opacity() == pytest.approx(1.0)


def test_circuit_group_highlights_members_in_place():
    r1, r2 = Resistor([0, 0], [2, 0]), Resistor([2, 0], [2, -2])
    CircuitGroup(r1, r2).highlight("accent_cool")
    cool = ManimColor(T().role("accent_cool")).to_hex().lower()
    assert r2.body.get_stroke_color().to_hex().lower() == cool


def test_layout_check_measures_schematic_parts_not_the_whole_box():
    from docker.chalkboard_base import _flatten_plain_groups, _is_connector

    inv = CmosInverter([0, 0], load=True)
    parts = _flatten_plain_groups([inv])
    assert len(parts) > 10
    texts = [m for m in parts if isinstance(m, _TEXTUAL)]
    assert texts, "labels are measured on their own"
    wires = [m for m in parts if not isinstance(m, _TEXTUAL) and len(m.points) > 0
             and m.get_fill_opacity() == 0 and m.get_stroke_opacity() > 0]
    assert wires and all(_is_connector(m) for m in wires)


def test_theme_colors_come_from_tokens():
    light = T(theme="light")
    r = Resistor([0, 0], [2, 0], theme="light")
    assert r.body.get_stroke_color().to_hex().lower() == ManimColor(light.role("stroke")).to_hex().lower()
    assert r.body.get_stroke_width() == light.stroke_width("normal")
    assert Capacitor([0, 0], [0, 1]).plates[0].get_stroke_width() == T().stroke_width("bold")
