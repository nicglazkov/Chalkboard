# tests/test_chalkboard_style.py
"""House-style patches applied by docker/chalkboard_style.py: tick label
decimals that follow the axis step (run 3253240e printed "0, 1, 2, 2, 2" on a
0 to 2.5 V axis in 0.5 V steps)."""
import pytest

manim = pytest.importorskip("manim")

from manim import Axes, NumberLine  # noqa: E402
from docker import chalkboard_style as style  # noqa: E402


def _labels(number_line) -> list[str]:
    """The tick label strings exactly as Manim typesets them."""
    d = number_line.decimal_number_config["num_decimal_places"]
    return [f"{float(n.number):.{d}f}" for n in number_line.numbers]


@pytest.mark.parametrize("value,places", [
    (2, 0), (2.0, 0), (0.5, 1), (0.25, 2), (0.1 + 0.2, 1), (2.5, 1),
    (0.125, 3), (1 / 3, 3), (-1.5, 1), (1e6, 0),
])
def test_decimals_for(value, places):
    assert style.decimals_for(value) == places


@pytest.mark.parametrize("x_range,places", [
    ([0, 2.5, 0.5], 1), ([0, 6, 1], 0), ([0, 6, 1.0], 0), ([0, 1, 0.25], 2),
    ([0.25, 2, 0.5], 2), ([0, 10], 0), (None, None), ([0], None),
])
def test_tick_decimal_places(x_range, places):
    assert style.tick_decimal_places(x_range) == places


def test_patch_is_installed():
    assert getattr(NumberLine.__init__, "_chalkboard_patch", False)
    before = NumberLine.__init__
    style.apply()  # idempotent: never wraps twice
    assert NumberLine.__init__ is before


def test_raw_axes_with_explicit_zero_decimals_on_half_steps_reads_correctly():
    """The 3253240e bug shape: num_decimal_places=0 on a 0.5 step."""
    ax = Axes(x_range=[0, 6, 1], y_range=[0, 2.5, 0.5],
              axis_config={"include_numbers": True,
                           "decimal_number_config": {"num_decimal_places": 0}})
    assert _labels(ax.y_axis) == ["0.5", "1.0", "1.5", "2.0", "2.5"]
    assert _labels(ax.x_axis) == ["1", "2", "3", "4", "5", "6"]


def test_raw_number_line_float_integer_step_prints_integers():
    """Manim alone infers decimals from str(step): 1.0 would print "1.0"."""
    nl = NumberLine(x_range=[0, 4, 1.0], include_numbers=True)
    assert _labels(nl) == ["0", "1", "2", "3", "4"]


def test_larger_explicit_decimals_are_kept():
    nl = NumberLine(x_range=[0, 1, 0.5], include_numbers=True,
                    decimal_number_config={"num_decimal_places": 2})
    assert _labels(nl) == ["0.00", "0.50", "1.00"]


def test_positional_range_and_quarter_steps():
    nl = NumberLine([0, 1, 0.25], include_numbers=True)
    assert _labels(nl) == ["0.00", "0.25", "0.50", "0.75", "1.00"]


def test_log_axes_untouched():
    from manim import LogBase
    ax = Axes(x_range=[0, 3, 1], y_range=[-2, 2, 1],
              y_axis_config={"scaling": LogBase(), "include_numbers": True})
    assert ax.y_axis.decimal_number_config.get("num_decimal_places", 0) == 0
