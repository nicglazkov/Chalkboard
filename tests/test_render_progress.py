# tests/test_render_progress.py
from pathlib import Path
from main import _count_animations, _parse_manim_line


def test_count_animations_counts_self_play_calls(tmp_path):
    scene = tmp_path / "scene.py"
    scene.write_text(
        "class ChalkboardScene(Scene):\n"
        "    def construct(self):\n"
        "        self.play(Write(t), run_time=1.0)\n"
        "        self.play(FadeIn(x))\n"
        "        self.play(Transform(a, b), run_time=2.0)\n"
    )
    assert _count_animations(scene) == 3


def test_count_animations_returns_zero_for_missing_file(tmp_path):
    assert _count_animations(tmp_path / "nonexistent.py") == 0


def test_parse_manim_line_returns_animation_number():
    line = "Animation 5 : Partial movie file written in /output/abc/..."
    assert _parse_manim_line(line) == 5


def test_parse_manim_line_returns_none_for_non_progress_lines():
    assert _parse_manim_line("Rendering ChalkboardScene...") is None
    assert _parse_manim_line("RENDER_COMPLETE:/output/abc/video.mp4") is None
    assert _parse_manim_line("") is None
    assert _parse_manim_line("Animation without colon") is None
