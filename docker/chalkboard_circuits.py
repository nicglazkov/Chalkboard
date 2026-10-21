r"""Chalkboard circuit schematics.

Schematic symbols drawn the way a textbook draws them: zigzag resistors,
parallel-plate capacitors, sources, ground, switches, MOSFETs, and a few
standard arrangements built from them (CMOS inverter, resistor-load
inverter, inverter chain, pass transistor driving a load capacitor).

Every symbol:

  * Is a `_ChalkComponent` (a VGroup), so it takes `theme=`, reveals with
    Create / FadeIn, and has `.highlight(role)` / `.mute()`.
  * Draws with token colors and stroke widths (wires `normal`, capacitor
    plates and MOSFET gate/channel `bold`).
  * Exposes named TERMINALS: `sym.terminal("d")` returns the point where a
    wire attaches, and `sym.terminals` maps every name to its point. The
    points are tracked by invisible anchors inside the group, so they stay
    correct after `.move_to()`, `.scale()` or `.shift()`.
  * Places its label (LaTeX, via math_tex) beside the body, never on a wire.

Two-terminal symbols are built between two points, so a schematic is laid
out by coordinates and then wired terminal to terminal:

    src = VSource(LEFT * 3 + DOWN * 1.5, LEFT * 3 + UP * 1.5, label=r"V_S = 5\,\mathrm{V}")
    r1 = Resistor(LEFT * 3 + UP * 1.5, UP * 1.5, label=r"R_1")
    r2 = Resistor(UP * 1.5, DOWN * 1.5, label=r"R_2")
    ret = Wire(r2.terminal("a"), src.terminal("a"))
    out = Junction(ORIGIN + UP * 1.5, label=r"V_{\mathrm{out}}", direction=UR)
"""
from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

try:
    from chalkboard_components import (  # type: ignore[import-not-found]
        _ChalkComponent,
        _TEXTUAL,
        math_tex,
    )
except ImportError:  # pipeline-side / pytest path
    from docker.chalkboard_components import _ChalkComponent, _TEXTUAL, math_tex  # noqa: F401

from manim import (
    Arrow,
    Circle,
    DOWN,
    Dot,
    LEFT,
    Line,
    Polygon,
    RIGHT,
    UP,
    UR,
    VGroup,
    VMobject,
)
from manim.utils.color import ManimColor


# ── Geometry (canvas units) ───────────────────────────────────────────
#
# Sized so a five-element schematic is about 4 to 6 units wide and every
# symbol reads at 1080p. Shared so mixed symbols line up.

RES_BODY = 1.0        # zigzag length
RES_AMP = 0.17        # zigzag half-height
RES_ZIGS = 6          # full zig periods (12 vertices)
CAP_GAP = 0.18        # plate separation
CAP_PLATE = 0.7       # plate length
SRC_RADIUS = 0.4      # voltage source circle
GND_WIDTHS = (0.56, 0.36, 0.16)
GND_STEP = 0.11       # vertical spacing between ground bars
GND_STEM = 0.22
RAIL_STEM = 0.22
RAIL_WIDTH = 0.56
DOT_RADIUS = 0.07     # junction dot
PORT_RADIUS = 0.08    # open terminal (hollow circle)
FET_HEIGHT = 1.4      # MOSFET drain-to-source span
FET_LEAD_X = 0.45     # drain/source lead offset from the channel
FET_GATE_GAP = 0.2    # gate plate to channel (the oxide)
FET_GATE_LEAD = 0.55  # gate lead length beyond the plate
BUBBLE_RADIUS = 0.09
GATE_SIZE = 0.8       # inverter-gate triangle width and height
GATE_LEAD = 0.3


def _v(p) -> np.ndarray:
    """Any 2- or 3-component point (list, tuple, ndarray) as a 3-vector."""
    a = np.array(p, dtype=float).reshape(-1)
    if a.shape[0] == 2:
        a = np.array([a[0], a[1], 0.0])
    return a[:3]


def _unit(d) -> np.ndarray:
    n = float(np.linalg.norm(d))
    if n < 1e-9:
        raise ValueError("circuit element endpoints coincide; give two distinct points")
    return d / n


def _normal(u) -> np.ndarray:
    """u rotated 90 degrees counter-clockwise (the left side of a->b)."""
    return np.array([-u[1], u[0], 0.0])


def _auto_label_dir(u) -> np.ndarray:
    """Default label side: above a horizontal element, right of a vertical one."""
    return UP if abs(u[0]) >= abs(u[1]) else RIGHT


class _Anchor(VMobject):
    """An invisible, zero-size point that rides along with its group's
    transforms, so a terminal stays where the drawn lead ends."""

    def __init__(self, point, **kwargs):
        super().__init__(stroke_opacity=0.0, fill_opacity=0.0, **kwargs)
        p = _v(point)
        self.set_points_as_corners([p, p])
        self._cb_anchor = True

    def point(self) -> np.ndarray:
        return self.points[0].copy()


class _Circuit(_ChalkComponent):
    """Shared base for schematic symbols: terminals, labels, and a
    highlight / mute that reaches every stroke and dot in the family
    (composite symbols nest other symbols)."""

    _cb_flatten = True  # layout check: measure the parts, not the whole box

    def __init__(self, *, theme: str = "chalkboard", role: str = "stroke"):
        super().__init__(theme=theme)
        self._role = role
        self._anchors: dict[str, _Anchor] = {}
        self.label = None

    # ── building helpers ─────────────────────────────────────────

    @property
    def _color(self):
        return ManimColor(self._t.role(self._role))

    def _line(self, a, b, weight: str = "normal") -> Line:
        return Line(_v(a), _v(b), color=self._color, stroke_width=self._t.stroke_width(weight))

    def _poly(self, pts, weight: str = "normal") -> VMobject:
        m = VMobject(stroke_color=self._color, stroke_width=self._t.stroke_width(weight))
        m.set_points_as_corners([_v(p) for p in pts])
        return m

    def _dot(self, p) -> Dot:
        return Dot(_v(p), radius=DOT_RADIUS, color=self._color)

    def _port(self, p) -> Circle:
        c = Circle(radius=PORT_RADIUS, stroke_color=self._color,
                   stroke_width=self._t.stroke_width("normal"), fill_opacity=0.0)
        c.move_to(_v(p))
        return c

    def _add_terminal(self, name: str, point) -> None:
        a = _Anchor(point)
        self._anchors[name] = a
        self.add(a)

    def _add_label(self, label: Optional[str], target, direction, *, role: str = "body",
                   size: str = "body", buff: Optional[str] = "sm"):
        """Typeset `label` and place it next to `target` (a mobject or point)
        on `direction`, outside the drawn body. Returns the label or None."""
        if not label:
            return None
        m = math_tex(label, size=size, role=role, theme=self.theme)
        m.next_to(target, direction, buff=self._t.space(buff))
        self.add(m)
        return m

    # ── public API ───────────────────────────────────────────────

    def terminal(self, name: str) -> np.ndarray:
        """Canvas point of terminal `name`, where a wire attaches."""
        try:
            return self._anchors[name].point()
        except KeyError:
            raise KeyError(f"{type(self).__name__} has no terminal {name!r}; "
                           f"terminals: {sorted(self._anchors)}") from None

    @property
    def terminals(self) -> dict:
        """{name: point} for every terminal."""
        return {k: a.point() for k, a in self._anchors.items()}

    # ── highlight / mute across nested symbols ───────────────────

    def _walk(self):
        strokes, texts = [], []

        def visit(m):
            for sm in m.submobjects:
                if isinstance(sm, _TEXTUAL):
                    texts.append(sm)
                elif getattr(sm, "_cb_anchor", False):
                    continue
                elif len(sm.points) > 0:
                    strokes.append(sm)
                else:
                    visit(sm)

        visit(self)
        return strokes, texts

    def _stroke_targets(self):
        return self._walk()[0]

    def _text_targets(self):
        return self._walk()[1]

    def _paint(self, role: str, weight: str) -> None:
        color = ManimColor(self._t.role(role))
        strokes, texts = self._walk()
        for m in strokes:
            bold = getattr(m, "_cb_weight", "normal") == "bold"
            m.set_stroke(color=color, width=self._t.stroke_width("bold" if bold else weight))
            m.set_fill(color=color)  # dots, arrow tips; opacity unchanged
        for m in texts:
            m.set_color(color)

    def highlight(self, role: str = "focus_primary") -> "_Circuit":
        """Color every stroke, dot and label with `role`, strokes bold
        (undoes a previous mute)."""
        self._restore_opacity()
        self._paint(role, "bold")
        return self

    def mute(self) -> "_Circuit":
        """Recede: context_muted color, normal strokes, dimmed opacity."""
        self._paint("context_muted", "normal")
        self._dim(self._MUTE_FACTOR)
        return self


def _bold(m):
    """Mark a stroke that stays bold through mute() (plates, gate, channel)."""
    m._cb_weight = "bold"
    return m


class CircuitGroup(_Circuit):
    """Several schematic parts treated as one for emphasis: a current path,
    a pull-up network, the loop a KVL step walks. Members stay wherever
    they already are (also in their own schematic):

        path = CircuitGroup(src, r1, r2, ret)
        self.play(path.animate.highlight("focus_primary"))
    """

    def __init__(self, *parts, role: str = "stroke", theme: str = "chalkboard"):
        super().__init__(theme=theme, role=role)
        self.add(*parts)


# ── Wires and nodes ──────────────────────────────────────────────────


class Wire(_Circuit):
    """A wire through two or more points with square corners.

        Wire(a, b)                    straight
        Wire(a, b, route="hv")        horizontal first, then vertical (one corner)
        Wire(a, b, route="vh")        vertical first, then horizontal
        Wire(a, p1, p2, b)            any polyline
        Wire(a, b, dots=[a])          filled junction dot at a (a node where 3+ wires meet)

    Terminals "start" and "end". `.path` is the drawn VMobject (for
    MoveAlongPath or ShowPassingFlash along the wire).
    """

    def __init__(self, *points, route: Optional[str] = None, dots: Sequence = (),
                 role: str = "stroke", theme: str = "chalkboard"):
        super().__init__(theme=theme, role=role)
        pts = [_v(p) for p in points]
        if len(pts) < 2:
            raise ValueError("Wire needs at least two points")
        if route is not None:
            if len(pts) != 2 or route not in ("hv", "vh"):
                raise ValueError('route="hv" or "vh" takes exactly two points')
            a, b = pts
            corner = np.array([b[0], a[1], 0.0]) if route == "hv" else np.array([a[0], b[1], 0.0])
            pts = [a, corner, b]
        self.path = self._poly(pts)
        self.add(self.path)
        self.dots = VGroup(*[self._dot(p) for p in dots])
        if dots:
            self.add(self.dots)
        self._add_terminal("start", pts[0])
        self._add_terminal("end", pts[-1])


class Junction(_Circuit):
    r"""A circuit node: filled dot where wires join (open=False) or a hollow
    circle for an input/output terminal (open=True), with an optional
    label beside it (e.g. r"V_{\mathrm{out}}") on `direction`. Terminal "p"."""

    def __init__(self, point, label: Optional[str] = None, *, direction=RIGHT,
                 open: bool = False, label_role: str = "body",
                 role: str = "stroke", theme: str = "chalkboard"):
        super().__init__(theme=theme, role=role)
        p = _v(point)
        self.dot = self._port(p) if open else self._dot(p)
        self.add(self.dot)
        self._add_terminal("p", p)
        self.label = self._add_label(label, self.dot, direction, role=label_role)


class CurrentArrow(_Circuit):
    """Current direction beside a wire segment a -> b: a short arrow
    parallel to the wire, offset to `side` ("left" or "right" of travel,
    default: above a horizontal wire, right of a vertical one), with an
    optional label (r"I", r"i_C") beyond it."""

    def __init__(self, a, b, label: Optional[str] = None, *, side: Optional[str] = None,
                 length: float = 0.8, role: str = "accent_cool", theme: str = "chalkboard"):
        super().__init__(theme=theme, role=role)
        a, b = _v(a), _v(b)
        u = _unit(b - a)
        n = _normal(u)
        if side is None:
            side_dir = _auto_label_dir(u)
            sign = 1.0 if float(np.dot(n, side_dir)) >= 0 else -1.0
        else:
            sign = 1.0 if side == "left" else -1.0
        off = n * sign * self._t.space("sm")
        mid = (a + b) / 2 + off
        half = min(length, float(np.linalg.norm(b - a))) / 2
        self.arrow = Arrow(mid - u * half, mid + u * half, buff=0.0, color=self._color,
                           stroke_width=self._t.stroke_width("normal"),
                           max_tip_length_to_length_ratio=0.3)
        self.add(self.arrow)
        self.label = self._add_label(label, self.arrow, n * sign, role=role, size="caption",
                                     buff="xs")


# ── Two-terminal elements ────────────────────────────────────────────


class _TwoTerminal(_Circuit):
    """Element between points a and b: leads from each point to the body,
    which is centred on the segment. Terminals "a" and "b"."""

    body_length = RES_BODY

    def __init__(self, a, b, label: Optional[str] = None, *, label_dir=None,
                 label_role: str = "body", role: str = "stroke", theme: str = "chalkboard"):
        super().__init__(theme=theme, role=role)
        self.a_pt, self.b_pt = _v(a), _v(b)
        d = self.b_pt - self.a_pt
        self.u = _unit(d)
        self.n = _normal(self.u)
        span = float(np.linalg.norm(d))
        body = min(self.body_length, span * 0.8)
        mid = (self.a_pt + self.b_pt) / 2
        self.s = mid - self.u * body / 2   # body start
        self.e = mid + self.u * body / 2   # body end
        self.body = self._build_body(body)
        self.leads = VGroup(self._line(self.a_pt, self.s), self._line(self.e, self.b_pt))
        self.add(self.leads, self.body)
        self._add_terminal("a", self.a_pt)
        self._add_terminal("b", self.b_pt)
        self.label = self._add_label(
            label, self.body, _v(label_dir) if label_dir is not None else _auto_label_dir(self.u),
            role=label_role)

    def _build_body(self, length: float):
        raise NotImplementedError


class Resistor(_TwoTerminal):
    """Zigzag resistor from a to b (any direction). Label e.g. r"R_1" or
    r"R_1 = 2\\,\\mathrm{k\\Omega}"; it sits beside the zigzag."""

    def _build_body(self, length: float):
        n_pts = 2 * RES_ZIGS
        step = (self.e - self.s) / n_pts
        pts = [self.s]
        for i in range(n_pts):
            sign = 1.0 if i % 2 == 0 else -1.0
            pts.append(self.s + step * (i + 0.5) + self.n * RES_AMP * sign)
        pts.append(self.e)
        return self._poly(pts)


class Capacitor(_TwoTerminal):
    """Two parallel plates across the a -> b axis with a gap between them.
    Label e.g. r"C_L"."""

    body_length = CAP_GAP

    def _build_body(self, length: float):
        half = self.n * CAP_PLATE / 2
        self.plates = VGroup(
            _bold(self._line(self.s - half, self.s + half, "bold")),
            _bold(self._line(self.e - half, self.e + half, "bold")),
        )
        return self.plates


class VSource(_TwoTerminal):
    """DC voltage source: a circle with + toward `pos` and - toward `neg`.
    Built from the negative terminal to the positive one, so terminal "a"
    is - and "b" is +. Label e.g. r"V_S = 5\\,\\mathrm{V}"."""

    body_length = 2 * SRC_RADIUS

    def __init__(self, neg, pos, label: Optional[str] = None, **kwargs):
        super().__init__(neg, pos, label, **kwargs)

    def _build_body(self, length: float):
        r = length / 2
        center = (self.s + self.e) / 2
        circle = Circle(radius=r, stroke_color=self._color,
                        stroke_width=self._t.stroke_width("normal"), fill_opacity=0.0)
        circle.move_to(center)
        plus = math_tex("+", size="caption", role=self._role, theme=self.theme)
        minus = math_tex("-", size="caption", role=self._role, theme=self.theme)
        plus.move_to(center + self.u * r * 0.5)
        minus.move_to(center - self.u * r * 0.5)
        self.circle = circle
        return VGroup(circle, plus, minus)


class Switch(_TwoTerminal):
    """A switch between a and b: two contacts and a blade hinged at the a
    side, tilted open or lying closed. `.set_closed(True)` moves the blade,
    so `self.play(sw.animate.set_closed(True))` animates the switch closing."""

    body_length = 0.8

    def __init__(self, a, b, label: Optional[str] = None, *, closed: bool = False, **kwargs):
        self._closed = closed
        super().__init__(a, b, label, **kwargs)

    def _blade_tip(self, closed: bool):
        if closed:
            return self.e
        return self.s + (self.e - self.s) * 0.92 + self.n * 0.38

    def _build_body(self, length: float):
        self.contacts = VGroup(self._port(self.s), self._port(self.e))
        self.blade = self._line(self.s, self._blade_tip(self._closed))
        return VGroup(self.contacts, self.blade)

    @property
    def closed(self) -> bool:
        return self._closed

    def set_closed(self, closed: bool = True) -> "Switch":
        s = self.contacts[0].get_center()
        e = self.contacts[1].get_center()
        u = _unit(e - s)
        tip = e if closed else s + (e - s) * 0.92 + _normal(u) * 0.38 * (
            float(np.linalg.norm(e - s)) / 0.8)
        self.blade.put_start_and_end_on(s, tip)
        self._closed = closed
        return self


# ── Sources of reference ─────────────────────────────────────────────


class Ground(_Circuit):
    """Ground symbol hanging below point p (three shrinking bars).
    Terminal "p"."""

    def __init__(self, point, *, role: str = "stroke", theme: str = "chalkboard"):
        super().__init__(theme=theme, role=role)
        p = _v(point)
        top = p + DOWN * GND_STEM
        parts = [self._line(p, top)]
        for i, w in enumerate(GND_WIDTHS):
            y = top + DOWN * GND_STEP * i
            parts.append(self._line(y + LEFT * w / 2, y + RIGHT * w / 2))
        self.add(*parts)
        self._add_terminal("p", p)


class Supply(_Circuit):
    """Supply rail: a short stem up from p to a bar, labelled above
    (default r"V_{DD}"). Terminal "p"."""

    def __init__(self, point, label: Optional[str] = r"V_{DD}", *, label_role: str = "body",
                 role: str = "stroke", theme: str = "chalkboard"):
        super().__init__(theme=theme, role=role)
        p = _v(point)
        top = p + UP * RAIL_STEM
        self.bar = self._line(top + LEFT * RAIL_WIDTH / 2, top + RIGHT * RAIL_WIDTH / 2)
        self.add(self._line(p, top), self.bar)
        self._add_terminal("p", p)
        self.label = self._add_label(label, self.bar, UP, role=label_role, buff="xs")


# ── Transistors ──────────────────────────────────────────────────────


def _rot(deg: float) -> np.ndarray:
    a = np.deg2rad(deg)
    return np.array([[np.cos(a), -np.sin(a), 0.0], [np.sin(a), np.cos(a), 0.0], [0.0, 0.0, 1.0]])


class Mosfet(_Circuit):
    """Enhancement MOSFET symbol: gate plate, oxide gap, channel, drain and
    source leads; PMOS (kind="p") has the bubble on the gate.

    Default orientation is vertical with the gate lead coming in from the
    left. NMOS has its drain on top and source at the bottom; PMOS has its
    source on top (toward VDD) and drain at the bottom, as in a CMOS
    pull-up. `flip=True` swaps drain and source; `mirror=True` brings the
    gate in from the right; `angle` rotates the whole symbol (degrees,
    counter-clockwise: angle=-90 gives a horizontal pass transistor with
    the gate on top and drain/source left and right).

    Terminals "g", "d", "s" (and "b" when body=True: a body lead off the
    channel). terminal_labels=True writes g / d / s beside the leads.
    `label` (e.g. r"M_1", r"\\mathrm{NMOS}") sits on the side away from
    the gate.
    """

    def __init__(self, center=(0.0, 0.0), kind: str = "n", *, label: Optional[str] = None,
                 body: bool = False, flip: bool = False, mirror: bool = False,
                 angle: float = 0.0, terminal_labels: bool = False, label_role: str = "body",
                 role: str = "stroke", theme: str = "chalkboard"):
        if kind not in ("n", "p"):
            raise ValueError('Mosfet kind is "n" or "p"')
        super().__init__(theme=theme, role=role)
        self.kind = kind
        c = _v(center)
        sx = -1.0 if mirror else 1.0
        R = _rot(angle)

        def P(x, y):  # local -> canvas
            return c + R @ np.array([x * sx, y, 0.0])

        def D(x, y):  # local direction -> canvas
            return R @ np.array([x * sx, y, 0.0])

        h = FET_HEIGHT / 2
        ch, ox, lx = 0.32, FET_GATE_GAP, FET_LEAD_X
        gx = -ox
        self.channel = _bold(self._line(P(0, 0.4), P(0, -0.4), "bold"))
        self.gate_plate = _bold(self._line(P(gx, ch), P(gx, -ch), "bold"))
        top_lead = self._poly([P(0, ch), P(lx, ch), P(lx, h)])
        bot_lead = self._poly([P(0, -ch), P(lx, -ch), P(lx, -h)])
        g_end = P(gx - FET_GATE_LEAD, 0)
        if kind == "p":
            self.bubble = Circle(radius=BUBBLE_RADIUS, stroke_color=self._color,
                                 stroke_width=self._t.stroke_width("normal"), fill_opacity=0.0)
            self.bubble.move_to(P(gx - BUBBLE_RADIUS, 0))
            gate_lead = self._line(P(gx - 2 * BUBBLE_RADIUS, 0), g_end)
            self.add(self.bubble)
        else:
            self.bubble = None
            gate_lead = self._line(P(gx, 0), g_end)
        self.gate_lead = gate_lead
        self.add(self.channel, self.gate_plate, top_lead, bot_lead, gate_lead)

        top_name, bot_name = ("d", "s") if kind == "n" else ("s", "d")
        if flip:
            top_name, bot_name = bot_name, top_name
        leads = {top_name: top_lead, bot_name: bot_lead}
        self.drain, self.source = leads["d"], leads["s"]
        self._add_terminal("g", g_end)
        self._add_terminal(top_name, P(lx, h))
        self._add_terminal(bot_name, P(lx, -h))
        if body:
            self.body_lead = self._line(P(0, 0), P(lx + 0.35, 0))
            self.add(self.body_lead)
            self._add_terminal("b", P(lx + 0.35, 0))

        self.terminal_labels = VGroup()
        if terminal_labels:
            # beside the middle of each lead, never at its end where a wire attaches
            spots = {"g": (P(gx - FET_GATE_LEAD / 2, 0), D(0, 1)),
                     top_name: (P(lx, (ch + h) / 2), D(1, 0)),
                     bot_name: (P(lx, -(ch + h) / 2), D(1, 0))}
            for name, (spot, away) in spots.items():
                lab = math_tex(name, size="caption", role="context_muted", theme=self.theme)
                lab.next_to(spot, away, buff=self._t.space("xs"))
                self.terminal_labels.add(lab)
            self.add(self.terminal_labels)

        # label on the side away from the gate, level with the channel
        self.label = self._add_label(label, P(lx + (0.45 if body else 0.0), 0),
                                     D(1, 0), role=label_role)


class NMOS(Mosfet):
    """Mosfet(kind="n")."""

    def __init__(self, center=(0.0, 0.0), **kwargs):
        super().__init__(center, "n", **kwargs)


class PMOS(Mosfet):
    """Mosfet(kind="p"): gate bubble, source on top."""

    def __init__(self, center=(0.0, 0.0), **kwargs):
        super().__init__(center, "p", **kwargs)


# ── Standard arrangements ────────────────────────────────────────────


class CmosInverter(_Circuit):
    """Static CMOS inverter: PMOS on top from VDD to Out, NMOS below from
    Out to ground, gates tied together to In. load=True adds the load
    capacitor from Out to ground (label `load_label`, default r"C_L").

    About 4 units wide, 4.4 tall, centred on `center`. Terminals "in",
    "out", "vdd", "gnd". Parts for emphasis: `.pmos`, `.nmos`, `.cap`
    (or None), `.pull_up` (VDD rail through the PMOS to Out: highlight it
    to show the load charging) and `.pull_down` (Out through the NMOS to
    ground: the discharge path).
    """

    def __init__(self, center=(0.0, 0.0), *, load: bool = False, in_label: str = r"V_{\mathrm{in}}",
                 out_label: str = r"V_{\mathrm{out}}", vdd_label: str = r"V_{DD}",
                 load_label: str = r"C_L", terminal_labels: bool = False,
                 role: str = "stroke", theme: str = "chalkboard"):
        super().__init__(theme=theme, role=role)
        c = _v(center)
        kw = dict(role=role, theme=theme)
        self.pmos = PMOS(c + UP * 1.0, terminal_labels=terminal_labels, **kw)
        self.nmos = NMOS(c + DOWN * 1.0, terminal_labels=terminal_labels, **kw)
        p, n = self.pmos.terminals, self.nmos.terminals
        out_pt = (p["d"] + n["d"]) / 2
        self.vdd = Supply(p["s"], vdd_label, **kw)
        self.gnd = Ground(n["s"], **kw)
        mid_wire = Wire(p["d"], n["d"], **kw)
        gate_x = p["g"][0]
        in_node = np.array([gate_x, c[1], 0.0])
        gate_tie = Wire(p["g"], n["g"], dots=[in_node], **kw)
        in_end = in_node + LEFT * 1.0
        in_wire = Wire(in_end, in_node, **kw)
        self.in_port = Junction(in_end, in_label, direction=LEFT, open=True, **kw)
        out_end = out_pt + RIGHT * (1.9 if load else 1.2)
        self.out_dot = Junction(out_pt, **kw)
        parts = [self.pmos, self.nmos, self.vdd, self.gnd, mid_wire, gate_tie, in_wire,
                 self.in_port, self.out_dot]
        self.cap = None
        if load:
            tap = out_pt + RIGHT * 1.0
            cap_bot = tap + DOWN * 1.4
            self.cap = Capacitor(tap, cap_bot, load_label, **kw)
            self.cap_gnd = Ground(cap_bot, **kw)
            tap_dot = Junction(tap, **kw)
            out_wire = Wire(out_pt, out_end, **kw)
            parts += [out_wire, self.cap, self.cap_gnd, tap_dot]
        else:
            out_wire = Wire(out_pt, out_end, **kw)
            parts.append(out_wire)
        self.out_port = Junction(out_end, out_label, direction=RIGHT, open=True, **kw)
        parts.append(self.out_port)
        self.add(*parts)
        self.pull_up = CircuitGroup(self.vdd, self.pmos, theme=theme)
        self.pull_down = CircuitGroup(self.nmos, self.gnd, theme=theme)
        self._add_terminal("in", in_end)
        self._add_terminal("out", out_end)
        self._add_terminal("vdd", p["s"])
        self._add_terminal("gnd", n["s"])


class ResistorLoadInverter(_Circuit):
    """Resistive-load (NMOS) inverter: load resistor from VDD to Out, NMOS
    from Out to ground, input on the gate; load=True adds C_L from Out to
    ground. Terminals "in", "out", "vdd", "gnd". Parts: `.resistor`,
    `.nmos`, `.cap` (or None)."""

    def __init__(self, center=(0.0, 0.0), *, load: bool = False, r_label: str = r"R_L",
                 in_label: str = r"V_{\mathrm{in}}", out_label: str = r"V_{\mathrm{out}}",
                 vdd_label: str = r"V_{DD}", load_label: str = r"C_L",
                 terminal_labels: bool = False, role: str = "stroke", theme: str = "chalkboard"):
        super().__init__(theme=theme, role=role)
        c = _v(center)
        kw = dict(role=role, theme=theme)
        self.nmos = NMOS(c + DOWN * 1.0, terminal_labels=terminal_labels, **kw)
        n = self.nmos.terminals
        out_pt = n["d"] + UP * 0.3
        top = out_pt + UP * 1.6
        self.resistor = Resistor(top, out_pt, r_label, **kw)
        self.vdd = Supply(top, vdd_label, **kw)
        self.gnd = Ground(n["s"], **kw)
        drain_wire = Wire(out_pt, n["d"], **kw)
        in_end = n["g"] + LEFT * 1.0
        in_wire = Wire(in_end, n["g"], **kw)
        self.in_port = Junction(in_end, in_label, direction=LEFT, open=True, **kw)
        self.out_dot = Junction(out_pt, **kw)
        out_end = out_pt + RIGHT * (1.9 if load else 1.2)
        parts = [self.resistor, self.vdd, self.nmos, self.gnd, drain_wire, in_wire,
                 self.in_port, self.out_dot, Wire(out_pt, out_end, **kw)]
        self.cap = None
        if load:
            tap = out_pt + RIGHT * 1.0
            self.cap = Capacitor(tap, tap + DOWN * 1.4, load_label, **kw)
            parts += [self.cap, Ground(tap + DOWN * 1.4, **kw), Junction(tap, **kw)]
        self.out_port = Junction(out_end, out_label, direction=RIGHT, open=True, **kw)
        parts.append(self.out_port)
        self.add(*parts)
        self._add_terminal("in", in_end)
        self._add_terminal("out", out_end)
        self._add_terminal("vdd", top)
        self._add_terminal("gnd", n["s"])


class InverterGate(_Circuit):
    """Logic inverter (NOT gate): triangle pointing right with the output
    bubble, input lead on the left, output lead on the right. `label` is
    written above the triangle (a size such as "1", "4", r"f^2", or a
    name). Terminals "in", "out"."""

    def __init__(self, center=(0.0, 0.0), label: Optional[str] = None, *,
                 label_role: str = "body", role: str = "stroke", theme: str = "chalkboard"):
        super().__init__(theme=theme, role=role)
        c = _v(center)
        w = h = GATE_SIZE
        left = c + LEFT * w / 2
        tip = c + RIGHT * w / 2
        self.triangle = Polygon(left + UP * h / 2, left + DOWN * h / 2, tip,
                                stroke_color=self._color,
                                stroke_width=self._t.stroke_width("normal"), fill_opacity=0.0)
        self.bubble = Circle(radius=BUBBLE_RADIUS, stroke_color=self._color,
                             stroke_width=self._t.stroke_width("normal"), fill_opacity=0.0)
        self.bubble.move_to(tip + RIGHT * BUBBLE_RADIUS)
        out_start = tip + RIGHT * 2 * BUBBLE_RADIUS
        in_end = left + LEFT * GATE_LEAD
        out_end = out_start + RIGHT * GATE_LEAD
        self.add(self.triangle, self.bubble, self._line(in_end, left), self._line(out_start, out_end))
        self._add_terminal("in", in_end)
        self._add_terminal("out", out_end)
        self.label = self._add_label(label, self.triangle, UP, role=label_role, size="caption")


class InverterChain(_Circuit):
    """A chain of inverters (a buffer chain) left to right, one gate per
    entry of `sizes` (labels such as ["1", "4", "16"] or
    ["1", "f", "f^2"]), optional load capacitor on the output
    (`load_label`, e.g. r"C_L"; None for no load). The chain's output is
    inverted for an odd number of gates.

    Terminals "in", "out", and "n0", "n1", ... for the node after each
    gate. Parts: `.gates`, `.cap` (or None)."""

    def __init__(self, sizes: Sequence[str] = ("1", "4", "16"), *, center=(0.0, 0.0),
                 spacing: float = 1.7, load_label: Optional[str] = r"C_L",
                 in_label: Optional[str] = r"V_{\mathrm{in}}", out_label: Optional[str] = r"V_{\mathrm{out}}",
                 role: str = "stroke", theme: str = "chalkboard"):
        super().__init__(theme=theme, role=role)
        if not sizes:
            raise ValueError("InverterChain needs at least one gate")
        c = _v(center)
        kw = dict(role=role, theme=theme)
        k = len(sizes)
        x0 = -(k - 1) * spacing / 2
        self.gates = [InverterGate(c + RIGHT * (x0 + i * spacing), str(s), **kw)
                      for i, s in enumerate(sizes)]
        parts: list = list(self.gates)
        for i in range(k - 1):
            a, b = self.gates[i].terminal("out"), self.gates[i + 1].terminal("in")
            parts.append(Wire(a, b, **kw))
            self._add_terminal(f"n{i}", (a + b) / 2)
        in_end = self.gates[0].terminal("in") + LEFT * 0.5
        parts += [Wire(in_end, self.gates[0].terminal("in"), **kw),
                  Junction(in_end, in_label, direction=LEFT, open=True, **kw)]
        last = self.gates[-1].terminal("out")
        self.cap = None
        if load_label:
            tap = last + RIGHT * 0.6
            out_end = tap + RIGHT * 0.9
            self.cap = Capacitor(tap, tap + DOWN * 1.3, load_label, **kw)
            parts += [Wire(last, out_end, **kw), Junction(tap, **kw), self.cap,
                      Ground(tap + DOWN * 1.3, **kw)]
        else:
            out_end = last + RIGHT * 0.6
            parts.append(Wire(last, out_end, **kw))
        parts.append(Junction(out_end, out_label, direction=RIGHT, open=True, **kw))
        self.add(*parts)
        self._add_terminal("in", in_end)
        self._add_terminal(f"n{k - 1}", last)
        self._add_terminal("out", out_end)


class PassTransistor(_Circuit):
    """Pass-transistor arrangement: input on the left, the MOSFET lying
    horizontal with its gate on top (control voltage `gate_label`), the
    output node on the right with a load capacitor to ground. kind="n" or
    "p" (PMOS gate bubble). load_label=None drops the capacitor.

    Terminals "in", "out", "gate". Parts: `.fet`, `.cap` (or None),
    `.out_dot` (the V_out node, to highlight while it charges)."""

    def __init__(self, center=(0.0, 0.0), kind: str = "n", *, in_label: str = r"V_{\mathrm{in}}",
                 out_label: str = r"V_{\mathrm{out}}", gate_label: str = r"V_G",
                 load_label: Optional[str] = r"C", terminal_labels: bool = False,
                 role: str = "stroke", theme: str = "chalkboard"):
        super().__init__(theme=theme, role=role)
        c = _v(center)
        kw = dict(role=role, theme=theme)
        self.fet = Mosfet(c, kind, angle=-90, terminal_labels=terminal_labels, **kw)
        ft = self.fet.terminals
        # angle=-90 puts the top lead (NMOS drain, PMOS source) on the right
        right_name = "d" if kind == "n" else "s"
        left_name = "s" if kind == "n" else "d"
        left, right = ft[left_name], ft[right_name]
        in_end = left + LEFT * 1.0
        out_pt = right + RIGHT * 0.9
        gate_end = ft["g"] + UP * 0.4
        parts = [self.fet, Wire(in_end, left, **kw), Wire(right, out_pt, **kw),
                 Wire(ft["g"], gate_end, **kw),
                 Junction(in_end, in_label, direction=LEFT, open=True, **kw),
                 Junction(gate_end, gate_label, direction=UP, open=True, **kw)]
        self.out_dot = Junction(out_pt, out_label, direction=UR, **kw)
        parts.append(self.out_dot)
        self.cap = None
        if load_label:
            self.cap = Capacitor(out_pt, out_pt + DOWN * 1.3, load_label, **kw)
            parts += [self.cap, Ground(out_pt + DOWN * 1.3, **kw)]
        self.add(*parts)
        self._add_terminal("in", in_end)
        self._add_terminal("out", out_pt)
        self._add_terminal("gate", gate_end)



__all__ = [
    "CircuitGroup",
    "Wire",
    "Junction",
    "CurrentArrow",
    "Resistor",
    "Capacitor",
    "VSource",
    "Switch",
    "Ground",
    "Supply",
    "Mosfet",
    "NMOS",
    "PMOS",
    "CmosInverter",
    "ResistorLoadInverter",
    "InverterGate",
    "InverterChain",
    "PassTransistor",
]
