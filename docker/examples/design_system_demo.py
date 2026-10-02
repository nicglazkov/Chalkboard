"""Design-system smoke demo: tokens + components + moves + templates, math-heavy.

Render from the repo root (docker/ must be importable):

    PYTHONPATH=docker CHALKBOARD_REPORT_DIR=/tmp manim -ql docker/examples/design_system_demo.py DemoCalculus

Scenes: DemoDerivation (DerivationTemplate), DemoCalculus (axes + aligned
derivation + term emphasis), DemoLinearAlgebra (matrices), DemoAlgorithm
(AlgorithmTemplate). Each also writes layout_report.json, so the demo
doubles as a layout regression check.
"""
from chalkboard_base import ChalkboardSceneBase
from chalkboard_tokens import T
from chalkboard_components import (
    Callout, ChalkAxes, ChalkMatrix, EquationGroup, math_tex, resolve_motion, tex,
)
from chalkboard_moves import (
    cascade_reveal, derivation_step, emphasize_term, reveal_with_emphasis,
)
from chalkboard_templates import (
    AlgorithmTemplate, CodeTemplate, CompareTemplate, DerivationTemplate,
    HowtoTemplate, TimelineTemplate,
)
from manim import *

THEME = "chalkboard"


class DemoDerivation(ChalkboardSceneBase, Scene):
    def construct(self):
        t = T(theme=THEME)
        self.camera.background_color = t.bg
        DerivationTemplate(self, theme=THEME, beats={
            "title": r"Why $\frac{\dd}{\dd x} x^2 = 2x$",
            "lines": [
                r"f'(x) &= \lim_{h \to 0} \frac{(x+h)^2 - x^2}{h}",
                r"&= \lim_{h \to 0} \frac{2xh + h^2}{h}",
                r"&= \lim_{h \to 0} \left( 2x + h \right)",
                r"&= 2x",
            ],
            "steps": [
                {"note": "Start from the definition of the derivative"},
                {"note": r"Expand $(x+h)^2$; the $x^2$ terms cancel"},
                {"note": r"Divide every term by $h$"},
                {"emphasize": "2x", "note": r"As $h \to 0$ only $2x$ survives"},
            ],
            "colors": {"h": "accent_cool"},
        }).render_all([2.5, 2.5, 2.5, 3.0])
        self.end_layout_check()


class DemoCalculus(ChalkboardSceneBase, Scene):
    def construct(self):
        t = T(theme=THEME)
        self.camera.background_color = t.bg
        self.begin_segment(0, duration=12.0)
        title = tex(r"Area under $y = x^2$ from $0$ to $2$", size="title")
        title.move_to(UP * 3.4)
        reveal_with_emphasis(self, title, motion_name="snap")

        ax = ChalkAxes([0, 3, 1], [0, 9, 3], x_length=5.0, y_length=3.8,
                       x_label="x", y_label="y", theme=THEME)
        ax.move_to(LEFT * 3.6 + DOWN * 0.5)
        curve = ax.plot(lambda x: x ** 2, role="focus_secondary", x_range=[0, 3])
        area = ax.area(curve, x_range=[0, 2], role="accent_cool")
        label = math_tex(r"y = x^2", size="body", role="focus_secondary")
        label.next_to(ax.c2p(3, 9), LEFT, buff=t.space("sm"))
        reveal_with_emphasis(self, ax, motion_name="snap")
        self.play(Create(curve), **resolve_motion(t.motion("emphasis")))
        cascade_reveal(self, [area, label])

        eq = EquationGroup([
            r"\int_0^2 x^2 \, \dd x &= \left[ \frac{x^3}{3} \right]_0^2",
            r"&= \frac{2^3}{3} - \frac{0^3}{3}",
            r"&= \frac{8}{3}",
        ], colors={r"\frac{8}{3}": "focus_primary"}, isolate=[r"\frac{x^3}{3}"],
            size="body", theme=THEME)
        eq.move_to(RIGHT * 3.5 + UP * 0.2)
        derivation_step(self, eq, 0)
        emphasize_term(self, eq.lines[0], r"\frac{x^3}{3}", role="accent_warm", theme=THEME)
        derivation_step(self, eq, 1)
        derivation_step(self, eq, 2)
        note = tex(r"Antiderivative of $x^2$ is $x^3/3$", size="caption", role="accent_meta")
        note.move_to(DOWN * 3.3 + RIGHT * 3.5)
        reveal_with_emphasis(self, note)
        self.wait(1.0)
        self.end_layout_check()


class DemoLinearAlgebra(ChalkboardSceneBase, Scene):
    def construct(self):
        t = T(theme=THEME)
        self.camera.background_color = t.bg
        self.begin_segment(0, duration=12.0)
        title = tex(r"Matrix times vector: $A\vec{v} = \vec{w}$", size="title")
        title.move_to(UP * 3.4)
        reveal_with_emphasis(self, title, motion_name="snap")

        A = ChalkMatrix([[2, -1], [1, 3]], theme=THEME)
        v = ChalkMatrix([["x"], ["y"]], role="focus_secondary", theme=THEME)
        eq_sign = math_tex("=")
        w = ChalkMatrix([["2x - y"], ["x + 3y"]], theme=THEME)
        row = VGroup(A, v, eq_sign, w).arrange(RIGHT, buff=t.space("md"))
        row.move_to(UP * 0.6)
        cascade_reveal(self, [A, v, eq_sign, w])

        self.play(A.animate.highlight_row(0), w.animate.highlight_row(0),
                  **resolve_motion(t.motion("emphasis")))
        note = Callout("row 1 · column = first entry", w.get_right() + UP * (w.row(0).get_y() - w.get_y()),
                       role="accent_meta", offset=RIGHT * t.space("md"),
                       theme=THEME)
        reveal_with_emphasis(self, note)

        det = EquationGroup([
            r"\det A &= (2)(3) - (-1)(1)",
            r"&= 7",
        ], colors={"7": "focus_primary"}, size="body", theme=THEME)
        det.move_to(DOWN * 2.4)
        derivation_step(self, det, 0)
        derivation_step(self, det, 1)
        self.wait(1.0)
        self.end_layout_check()


class DemoAlgorithm(ChalkboardSceneBase, Scene):
    def construct(self):
        t = T(theme=THEME)
        self.camera.background_color = t.bg
        AlgorithmTemplate(self, theme=THEME, beats={
            "title": "Binary Search",
            "values": [3, 7, 11, 14, 18, 22, 27],
            "steps": [
                {"active_idx": 3, "callout": "Compare the middle element with the target"},
                {"active_idx": 5, "callout": "Target is larger: search the right half"},
                {"active_idx": 4, "callout": "Found it"},
            ],
        }).render_all([2.0, 2.5, 2.5, 2.5])
        self.end_layout_check()


class DemoCode(ChalkboardSceneBase, Scene):
    def construct(self):
        self.camera.background_color = T(theme=THEME).bg
        CodeTemplate(self, theme=THEME, beats={
            "title": "Factorial",
            "code_string": "def factorial(n):\n    if n <= 1:\n        return 1\n    return n * factorial(n - 1)",
            "steps": [
                {"line_indices": [0], "callout": "Signature"},
                {"line_indices": [1, 2], "callout": "Base case stops the recursion"},
                {"line_indices": [3], "callout": "Recursive case: n times (n - 1)!"},
            ],
        }).render_all([2.0, 2.5, 2.5, 2.5])
        self.end_layout_check()


class DemoCompare(ChalkboardSceneBase, Scene):
    def construct(self):
        self.camera.background_color = T(theme=THEME).bg
        CompareTemplate(self, theme=THEME, beats={
            "title": "Mean vs Median",
            "left": {"title": "Mean", "points": [r"$\bar{x} = \frac{1}{n}\sum_{i=1}^{n} x_i$",
                                                 "Pulled toward outliers"]},
            "right": {"title": "Median", "points": ["Middle value once sorted",
                                                    "Robust to outliers"]},
        }).render_all([2.0, 2.5, 2.5])
        self.end_layout_check()


class DemoHowto(ChalkboardSceneBase, Scene):
    def construct(self):
        self.camera.background_color = T(theme=THEME).bg
        HowtoTemplate(self, theme=THEME, beats={
            "title": "Solving a Linear System",
            "steps": [
                {"label": "Write the matrix", "description": r"Form the augmented matrix $[A \mid \vec{b}]$",
                 "callout": "Keep the right-hand side attached"},
                {"label": "Eliminate", "description": "Row-reduce to echelon form",
                 "callout": "Swap, scale, add multiples of rows"},
                {"label": "Back-substitute", "description": "Solve from the last row upward"},
            ],
        }).render_all([2.0, 2.5, 2.5, 2.5])
        self.end_layout_check()


class DemoTimeline(ChalkboardSceneBase, Scene):
    def construct(self):
        self.camera.background_color = T(theme=THEME).bg
        TimelineTemplate(self, theme=THEME, beats={
            "title": "History of Calculus",
            "events": [
                {"date": "1665", "label": "Newton", "description": "Method of fluxions"},
                {"date": "1675", "label": "Leibniz", "description": "Modern notation",
                 "callout": r"He introduced $\int$ and $\dd x$"},
                {"date": "1821", "label": "Cauchy", "description": "Rigorous limits"},
                {"date": "1854", "label": "Riemann", "description": "The Riemann integral"},
            ],
        }).render_all([2.0, 2.0, 2.5, 2.0, 2.5])
        self.end_layout_check()
