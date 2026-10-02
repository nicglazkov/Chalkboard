"""Shared base for template classes.

Holds the scene + beats + theme bookkeeping every template needs, strict
beats validation helpers, and small layout helpers (titles, text fitting,
segment timing). Subclasses implement `_validate_beats` (schema check)
and `render_all` (the animation sequence).
"""
from __future__ import annotations

from typing import Any, Optional

try:
    from chalkboard_tokens import T  # type: ignore[import-not-found]
except ImportError:
    from pipeline.design_tokens import T  # noqa: F401

try:
    from chalkboard_components import (  # type: ignore[import-not-found]
        math_tex as _math_tex,
        resolve_motion as _resolve_motion,
        tex as _tex,
    )
except ImportError:
    from docker.chalkboard_components import (  # noqa: F401
        math_tex as _math_tex,
        resolve_motion as _resolve_motion,
        tex as _tex,
    )

from manim import FadeIn, Text, UP

# Usable canvas (16:9 frame is 14.22 x 8.0) after margins.
CANVAS_SAFE_W = 13.0
CANVAS_X_LIMIT = 6.6


def scene_has_cue(scene, k: int) -> bool:
    """True when the scene's current segment has cue marker [[k]]."""
    has = getattr(scene, "has_cue", None)
    try:
        return callable(has) and has(k) is True
    except Exception:
        return False


class _TemplateBase:
    """Base class. Subclasses set NAME, REQUIRED_KEYS, optionally
    OPTIONAL_KEYS, then implement _validate_beats() and render_all().
    """

    NAME: str = ""
    REQUIRED_KEYS: tuple[str, ...] = ()
    OPTIONAL_KEYS: tuple[str, ...] = ()

    def __init__(self, scene, beats: dict, *, theme: str = "chalkboard") -> None:
        self.scene = scene
        self.beats = beats
        self.theme = theme
        self.t = T(theme=theme)
        self._validate_beats()
        # Templates cue the reveals they own; segments whose extra markers
        # have no template beat are not a layout error.
        try:
            scene._cues_template_driven = True
        except Exception:
            pass

    # ── shared validation helpers ─────────────────────────────────

    def _require(self, key: str, expected_type: type, *, of_items: Optional[type] = None) -> Any:
        """Strict key fetch from self.beats. Raises ValueError with a
        schema-mismatch message that flows back to manim_agent on retry.
        """
        if key not in self.beats:
            raise ValueError(
                f"{self.NAME}: beats missing required key {key!r}. "
                f"Required keys: {list(self.REQUIRED_KEYS)}. "
                f"Optional keys: {list(self.OPTIONAL_KEYS)}."
            )
        value = self.beats[key]
        if not isinstance(value, expected_type):
            raise ValueError(
                f"{self.NAME}: beats[{key!r}] must be {expected_type.__name__}, "
                f"got {type(value).__name__}."
            )
        if of_items is not None and isinstance(value, (list, tuple)):
            for i, item in enumerate(value):
                if not isinstance(item, of_items):
                    raise ValueError(
                        f"{self.NAME}: beats[{key!r}][{i}] must be "
                        f"{of_items.__name__}, got {type(item).__name__}."
                    )
        return value

    # ── layout / timing helpers ───────────────────────────────────

    def _motion(self, name: str) -> dict:
        return _resolve_motion(self.t.motion(name))

    def _rt(self, name: str) -> float:
        return self.t.motion(name)["run_time"]

    def _make_title(self, text: str, *, size: str = "title", y: float = 3.4):
        """Persistent title. `$...$` spans are typeset as math, so a title
        like "Why $\\frac{\\dd}{\\dd x} e^x = e^x$" never shows a raw caret."""
        t = self.t
        if "$" in text:
            title = _tex(text, size=size, role="body", theme=self.theme)
        else:
            title = Text(text, font_size=t.type(size), color=t.role("body")).set_color(t.role("body"))
        if title.width > CANVAS_SAFE_W:
            title.scale_to_fit_width(CANVAS_SAFE_W)
        title.move_to(UP * y)
        return title

    def _show_title(self, title) -> float:
        """Reveal the title at the top of segment 0; returns time used."""
        self.scene.play(FadeIn(title), **self._motion("snap"))
        return self._rt("snap")

    def _fit_text(self, text: str, *, size: str, role: str, max_width: float):
        """Text that never exceeds max_width: wrap once at the space nearest
        the middle, then scale down if still too wide. `$...$` spans are
        typeset as math."""
        t = self.t

        def build(s: str):
            stripped = s.strip()
            if stripped.startswith("$") and stripped.endswith("$") and stripped.count("$") == 2:
                # A point that is purely a formula is display math, so
                # fractions and sums get full size instead of inline style.
                # Scaled like tex() prose so it sits optically level with Text.
                return _math_tex(stripped[1:-1], size=size, role=role, theme=self.theme).scale(1.2)
            if "$" in s:
                return _tex(s, size=size, role=role, theme=self.theme)
            if "\n" in s:
                return Text(s, font_size=t.type(size), color=t.role(role), line_spacing=0.8).set_color(t.role(role))
            return Text(s, font_size=t.type(size), color=t.role(role)).set_color(t.role(role))

        obj = build(text)
        if obj.width > max_width and " " in text and "\n" not in text and "$" not in text:
            mid = len(text) // 2
            spaces = [i for i, c in enumerate(text) if c == " "]
            cut = min(spaces, key=lambda i: abs(i - mid))
            obj = build(text[:cut] + "\n" + text[cut + 1:])
        if obj.width > max_width:
            obj.scale_to_fit_width(max_width)
        return obj

    def _rest(self, duration: float, used: float) -> None:
        """Wait out the remainder of a segment (never wait(0)). Uses the
        scene's own narration clock when it has one, since cue waits and
        frame rounding make `used` an underestimate."""
        left = self._time_left()
        r = max(0.0, duration - used) if left is None else left
        if r > 0.02:
            self.scene.wait(r)

    def _time_left(self):
        fn = getattr(self.scene, "segment_time_left", None)
        try:
            v = fn() if callable(fn) else None
        except Exception:
            return None
        return float(v) if isinstance(v, (int, float)) else None

    def _cue(self, k: int = 1) -> float:
        """Hold for cue marker [[k]] of the current segment when it exists,
        so the next reveal lands on its word. Returns the seconds waited."""
        has = getattr(self.scene, "has_cue", None)
        try:
            if callable(has) and has(k) is True:
                before = self._time_left()
                self.scene.cue(k)
                after = self._time_left()
                if before is not None and after is not None:
                    return max(0.0, before - after)
        except Exception:
            pass
        return 0.0

    # ── overridable in subclasses ─────────────────────────────────

    def _validate_beats(self) -> None:
        raise NotImplementedError

    def render_all(self, segment_durations) -> None:
        """Emit the full animation sequence — every begin_segment, every
        play, every inter-segment cleanup. Called once from construct();
        the scene adds only end_layout_check + the final FadeOut.
        """
        raise NotImplementedError
