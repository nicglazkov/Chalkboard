# tests/test_chalkboard_templates.py
"""Phase 6 — template library.

Each template is a class that takes (scene, beats, theme) and exposes
render_all(segment_durations). These tests verify:

  * Constructor validates beats strictly (positive + negative cases)
  * render_all calls scene.begin_segment once per segment
  * render_all uses components + moves under the hood (sample-check
    via Manim animation types passed to scene.play)
  * Each template is independently importable

The whole file is gated on `pytest.importorskip("manim")` — without
Manim, the template modules fail at top-level import (they import
manim primitives). Locally these skip; in CI / regression / the
Docker render image they run.
"""
import pytest
from unittest.mock import MagicMock

manim = pytest.importorskip("manim")

from docker.chalkboard_templates import (
    AlgorithmTemplate,
    CodeTemplate,
    CompareTemplate,
    DerivationTemplate,
    HowtoTemplate,
    TimelineTemplate,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _mock_scene():
    """Returns a MagicMock with scene methods the templates touch."""
    s = MagicMock(spec=["play", "wait", "add", "remove", "begin_segment",
                        "get_mobject_family_members"])
    s.get_mobject_family_members.return_value = []
    return s


def _begin_segment_call_count(scene_mock) -> int:
    return scene_mock.begin_segment.call_count


# ── AlgorithmTemplate ────────────────────────────────────────────────


def test_algorithm_template_constructs_with_valid_beats():
    scene = _mock_scene()
    tmpl = AlgorithmTemplate(scene, beats={
        "title": "Binary Search",
        "values": [3, 7, 11, 14, 18, 22],
        "steps": [
            {"active_idx": 2, "callout": "Check middle"},
            {"active_idx": 4},
        ],
    })
    assert tmpl.beats["title"] == "Binary Search"
    assert len(tmpl.beats["steps"]) == 2


def test_algorithm_template_missing_required_key_raises():
    with pytest.raises(ValueError, match="missing required key"):
        AlgorithmTemplate(_mock_scene(), beats={
            "title": "X",
            "values": [1, 2, 3],
            # missing "steps"
        })


def test_algorithm_template_wrong_type_raises():
    with pytest.raises(ValueError, match="must be list"):
        AlgorithmTemplate(_mock_scene(), beats={
            "title": "X",
            "values": "not a list",
            "steps": [],
        })


def test_algorithm_template_active_idx_out_of_range_raises():
    with pytest.raises(ValueError, match="out of range"):
        AlgorithmTemplate(_mock_scene(), beats={
            "title": "X",
            "values": [1, 2],
            "steps": [{"active_idx": 99}],
        })


def test_algorithm_render_all_starts_each_segment():
    scene = _mock_scene()
    tmpl = AlgorithmTemplate(scene, beats={
        "title": "Test",
        "values": [1, 2, 3, 4],
        "steps": [
            {"active_idx": 0},
            {"active_idx": 1},
            {"active_idx": 2},
        ],
    })
    tmpl.render_all([2.0, 2.0, 2.0, 2.0])
    # Segment 0 (intro) + 3 step segments = 4 begin_segment calls
    assert _begin_segment_call_count(scene) == 4


def test_algorithm_render_all_callout_triggers_extra_play():
    """A step with a callout adds extra scene.play calls compared to
    one without.
    """
    no_callout = _mock_scene()
    AlgorithmTemplate(no_callout, beats={
        "title": "T", "values": [1, 2], "steps": [{"active_idx": 0}],
    }).render_all([2.0, 2.0])
    with_callout = _mock_scene()
    AlgorithmTemplate(with_callout, beats={
        "title": "T", "values": [1, 2],
        "steps": [{"active_idx": 0, "callout": "note"}],
    }).render_all([2.0, 2.0])
    assert with_callout.play.call_count > no_callout.play.call_count


# ── CodeTemplate ─────────────────────────────────────────────────────


def test_code_template_constructs_with_valid_beats():
    scene = _mock_scene()
    tmpl = CodeTemplate(scene, beats={
        "title": "T",
        "code_string": "x = 1\ny = 2\nprint(x + y)",
        "steps": [
            {"line_indices": [0], "callout": "Init x"},
            {"line_indices": [1, 2], "callout": "Print sum"},
        ],
    })
    assert tmpl.beats["code_string"].count("\n") == 2


def test_code_template_empty_line_indices_raises():
    with pytest.raises(ValueError, match="non-empty list"):
        CodeTemplate(_mock_scene(), beats={
            "title": "T",
            "code_string": "x = 1",
            "steps": [{"line_indices": []}],
        })


def test_code_template_out_of_range_line_raises():
    with pytest.raises(ValueError, match="out of range"):
        CodeTemplate(_mock_scene(), beats={
            "title": "T",
            "code_string": "x = 1\ny = 2",
            "steps": [{"line_indices": [99]}],
        })


def test_code_render_all_starts_each_segment():
    scene = _mock_scene()
    tmpl = CodeTemplate(scene, beats={
        "title": "T",
        "code_string": "a = 1\nb = 2\nprint(a + b)",
        "steps": [
            {"line_indices": [0]},
            {"line_indices": [1]},
        ],
    })
    tmpl.render_all([2.0, 2.0, 2.0])
    # Segment 0 (full reveal) + 2 highlight segments = 3
    assert _begin_segment_call_count(scene) == 3


# ── CompareTemplate ──────────────────────────────────────────────────


def test_compare_template_constructs_with_valid_beats():
    scene = _mock_scene()
    tmpl = CompareTemplate(scene, beats={
        "left":  {"title": "A", "points": ["p1", "p2"]},
        "right": {"title": "B", "points": ["q1", "q2"]},
    })
    assert len(tmpl.beats["left"]["points"]) == 2


def test_compare_template_mismatched_point_lengths_raises():
    with pytest.raises(ValueError, match="same length"):
        CompareTemplate(_mock_scene(), beats={
            "left":  {"title": "A", "points": ["p1", "p2"]},
            "right": {"title": "B", "points": ["q1"]},
        })


def test_compare_render_all_starts_each_segment():
    scene = _mock_scene()
    tmpl = CompareTemplate(scene, beats={
        "left":  {"title": "A", "points": ["p1", "p2"]},
        "right": {"title": "B", "points": ["q1", "q2"]},
    })
    tmpl.render_all([2.0, 2.0, 2.0])
    # Segment 0 (panel reveal) + 2 pair-reveal segments = 3
    assert _begin_segment_call_count(scene) == 3


# ── HowtoTemplate ────────────────────────────────────────────────────


def test_howto_template_constructs_with_valid_beats():
    scene = _mock_scene()
    tmpl = HowtoTemplate(scene, beats={
        "title": "T",
        "steps": [
            {"label": "Step A", "description": "Do thing A"},
            {"label": "Step B", "description": "Do thing B"},
        ],
    })
    assert len(tmpl.beats["steps"]) == 2


def test_howto_template_missing_label_raises():
    with pytest.raises(ValueError, match="'label'"):
        HowtoTemplate(_mock_scene(), beats={
            "title": "T",
            "steps": [{"description": "missing label"}],
        })


def test_howto_render_all_starts_each_segment():
    scene = _mock_scene()
    tmpl = HowtoTemplate(scene, beats={
        "title": "T",
        "steps": [
            {"label": "A", "description": "do A"},
            {"label": "B", "description": "do B"},
        ],
    })
    tmpl.render_all([2.0, 2.0, 2.0])
    assert _begin_segment_call_count(scene) == 3


# ── TimelineTemplate ─────────────────────────────────────────────────


def test_timeline_template_constructs_with_valid_beats():
    scene = _mock_scene()
    tmpl = TimelineTemplate(scene, beats={
        "title": "T",
        "events": [
            {"date": "1995", "label": "X"},
            {"date": "2005", "label": "Y", "description": "..."},
        ],
    })
    assert len(tmpl.beats["events"]) == 2


def test_timeline_template_missing_date_raises():
    with pytest.raises(ValueError, match="'date'"):
        TimelineTemplate(_mock_scene(), beats={
            "title": "T",
            "events": [{"label": "X"}],
        })


def test_timeline_render_all_starts_each_segment():
    scene = _mock_scene()
    tmpl = TimelineTemplate(scene, beats={
        "title": "T",
        "events": [
            {"date": "1900", "label": "A"},
            {"date": "1950", "label": "B"},
            {"date": "2000", "label": "C"},
        ],
    })
    tmpl.render_all([2.0, 2.0, 2.0, 2.0])
    # Segment 0 (axis) + 3 event-reveals = 4
    assert _begin_segment_call_count(scene) == 4


# ── Cross-cutting: theme binding ─────────────────────────────────────


def test_templates_bind_theme_to_T():
    """Every template should construct a T() namespace bound to the
    theme passed in, so all internal token lookups resolve correctly.
    """
    for tmpl_cls, beats in (
        (AlgorithmTemplate, {"title": "T", "values": [1], "steps": [{"active_idx": 0}]}),
        (CodeTemplate, {"title": "T", "code_string": "x", "steps": [{"line_indices": [0]}]}),
        (CompareTemplate, {"left": {"title": "A", "points": []}, "right": {"title": "B", "points": []}}),
        (HowtoTemplate, {"title": "T", "steps": []}),
        (TimelineTemplate, {"title": "T", "events": []}),
    ):
        t = tmpl_cls(_mock_scene(), beats=beats, theme="light")
        assert t.theme == "light"
        assert t.t.theme == "light"


# ── Manim_agent integration ──────────────────────────────────────────


def test_template_specs_reference_template_classes():
    """The TEMPLATE_SPECS prose in manim_agent must teach the agent to
    use the template classes — not the old prose recipe.
    """
    from pipeline.agents.manim_agent import TEMPLATE_SPECS
    for template_name, class_name in (
        ("algorithm", "AlgorithmTemplate"),
        ("code", "CodeTemplate"),
        ("compare", "CompareTemplate"),
        ("howto", "HowtoTemplate"),
        ("timeline", "TimelineTemplate"),
        ("derivation", "DerivationTemplate"),
    ):
        assert class_name in TEMPLATE_SPECS[template_name], (
            f"TEMPLATE_SPECS[{template_name!r}] must reference "
            f"{class_name!r}"
        )
        # And mention the render_all method so the agent knows the entry point.
        assert "render_all" in TEMPLATE_SPECS[template_name]


# ── DerivationTemplate ───────────────────────────────────────────────

_DERIV_BEATS = {
    "title": r"Why $\frac{\dd}{\dd x} x^2 = 2x$",
    "lines": [
        r"f'(x) &= \lim_{h \to 0} \frac{(x+h)^2 - x^2}{h}",
        r"&= \lim_{h \to 0} \frac{2xh + h^2}{h}",
        r"&= \lim_{h \to 0} (2x + h)",
        r"&= 2x",
    ],
    "steps": [
        {"note": "Definition of the derivative"},
        {"note": r"Expand $(x+h)^2$"},
        {"note": "Cancel one $h$", "emphasize": "h"},
        {"emphasize": "2x", "role": "focus_primary"},
    ],
    "colors": {"h": "accent_cool"},
}


def test_derivation_template_constructs_with_valid_beats():
    tmpl = DerivationTemplate(_mock_scene(), beats=_DERIV_BEATS)
    assert len(tmpl.beats["lines"]) == 4


def test_derivation_template_empty_lines_raises():
    with pytest.raises(ValueError, match="non-empty"):
        DerivationTemplate(_mock_scene(), beats={"title": "T", "lines": []})


def test_derivation_template_bad_role_raises():
    with pytest.raises(KeyError):
        DerivationTemplate(_mock_scene(), beats={
            "title": "T", "lines": ["a = b"], "colors": {"a": "not_a_role"},
        })


def test_derivation_render_all_starts_each_segment():
    scene = _mock_scene()
    DerivationTemplate(scene, beats=_DERIV_BEATS).render_all([2.0] * 4)
    assert _begin_segment_call_count(scene) == 4


def test_derivation_extra_lines_land_in_last_segment():
    scene = _mock_scene()
    tmpl = DerivationTemplate(scene, beats=_DERIV_BEATS)
    tmpl.render_all([2.0, 2.0])  # 4 lines, 2 segments
    assert _begin_segment_call_count(scene) == 2
    # every line was revealed (its morph / write was played)
    assert scene.play.call_count >= 4


# ── Real dry-run renders ─────────────────────────────────────────────
#
# The mock-scene tests above only check call shapes. These run each
# template inside a real ChalkboardScene with Manim's dry_run config
# (no frames written, but every mobject, TeX compile and animation is
# built) and assert the layout checker found no overlaps, off-canvas
# elements or zone collisions.

import json  # noqa: E402

from docker.chalkboard_base import ChalkboardSceneBase  # noqa: E402

_REAL_CASES = {
    "algorithm": (AlgorithmTemplate, {
        "title": "Binary Search",
        "values": [3, 7, 11, 14, 18, 22, 27],
        "steps": [
            {"active_idx": 3, "callout": "Compare the middle element with the target"},
            {"active_idx": 5, "callout": "Target is larger: search the right half"},
            {"active_idx": 4, "value_change": {"idx": 4, "to": 19}},
        ],
    }),
    "code": (CodeTemplate, {
        "title": "Factorial",
        "code_string": "def factorial(n):\n    if n <= 1:\n        return 1\n    return n * factorial(n - 1)",
        "steps": [
            {"line_indices": [0], "callout": "Signature"},
            {"line_indices": [1, 2], "callout": "Base case stops the recursion"},
            {"line_indices": [3]},
        ],
    }),
    "compare": (CompareTemplate, {
        "title": "Mean vs Median",
        "left": {"title": "Mean", "points": [r"$\bar{x} = \frac{1}{n}\sum_i x_i$", "Pulled by outliers"]},
        "right": {"title": "Median", "points": ["Middle value when sorted", "Robust to outliers"]},
    }),
    "howto": (HowtoTemplate, {
        "title": "Solving a Linear System",
        "steps": [
            {"label": "Write the matrix", "description": r"Form the augmented matrix $[A \mid b]$",
             "callout": "Keep the right-hand side attached"},
            {"label": "Eliminate", "description": "Row-reduce to echelon form"},
            {"label": "Back-substitute", "description": "Solve from the last row upward"},
        ],
    }),
    "timeline": (TimelineTemplate, {
        "title": "History of Calculus",
        "events": [
            {"date": "1665", "label": "Newton", "description": "Method of fluxions"},
            {"date": "1675", "label": "Leibniz", "description": "Modern notation", "callout": r"He introduced $\int$ and $\dd x$"},
            {"date": "1821", "label": "Cauchy", "description": "Rigorous limits"},
            {"date": "1854", "label": "Riemann", "description": "The Riemann integral"},
        ],
    }),
    "derivation": (DerivationTemplate, _DERIV_BEATS),
}


def _run_real(tmpl_cls, beats, tmp_path, n_seg=4):
    from manim import Scene, tempconfig

    class _S(ChalkboardSceneBase, Scene):
        _REPORT_DIR = str(tmp_path)

        def construct(self):
            tmpl_cls(self, beats=beats).render_all([3.0] * n_seg)
            self.end_layout_check()

    with tempconfig({"dry_run": True, "media_dir": str(tmp_path / "media"),
                     "verbosity": "ERROR", "disable_caching": True}):
        _S().render()
    return json.loads((tmp_path / "layout_report.json").read_text())


@pytest.mark.parametrize("name", sorted(_REAL_CASES))
def test_template_real_render_has_clean_layout(name, tmp_path):
    tmpl_cls, beats = _REAL_CASES[name]
    report = _run_real(tmpl_cls, beats, tmp_path)
    assert report["passed"], json.dumps(report["violations"], indent=2)
