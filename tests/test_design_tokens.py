# tests/test_design_tokens.py
"""Phase 1 — design tokens.

Locks the shape of the token table so future edits can't silently drop
a role (which would crash scenes that reference it) and can't introduce
a typo (which would slip past the prompt-injection block but blow up at
the t.role("foo") call site).
"""
import re

import pytest

from pipeline.design_tokens import (
    DEFAULT_THEME,
    TOKENS,
    T,
    render_prompt_block,
)


# ── Shape ────────────────────────────────────────────────────────────

_THEMES = ("chalkboard", "light", "colorful")

_SURFACE_KEYS = ("bg", "bg_subtle", "grid")
_ROLE_KEYS = (
    "focus_primary", "focus_secondary", "context_muted",
    "accent_warm", "accent_cool", "accent_meta",
    "body", "stroke", "stroke_muted",
)
_TYPE_KEYS = ("display", "title", "heading", "body", "caption", "code", "micro")
_SPACE_KEYS = ("xs", "sm", "md", "lg", "xl")
_STROKE_KEYS = ("hair", "normal", "bold")
_MOTION_KEYS = ("snap", "emphasis", "settle", "grand")
_LAG_KEYS = ("cascade", "quick")


def test_every_theme_present():
    """All declared themes are reachable through TOKENS and T()."""
    for theme in _THEMES:
        assert theme in TOKENS["themes"]
        # Constructable via T() — implicitly checks the Theme literal.
        T(theme=theme)


def test_default_theme_is_chalkboard():
    assert DEFAULT_THEME == "chalkboard"
    assert T().theme == "chalkboard"


@pytest.mark.parametrize("theme", _THEMES)
def test_surface_keys_complete(theme):
    """Each theme defines every surface key."""
    block = TOKENS["themes"][theme]["surface"]
    assert set(block) == set(_SURFACE_KEYS), (
        f"Theme {theme!r} surface keys diverge: "
        f"missing={set(_SURFACE_KEYS) - set(block)}, "
        f"extra={set(block) - set(_SURFACE_KEYS)}"
    )


@pytest.mark.parametrize("theme", _THEMES)
def test_role_keys_complete(theme):
    """Each theme defines every semantic role key."""
    block = TOKENS["themes"][theme]["role"]
    assert set(block) == set(_ROLE_KEYS), (
        f"Theme {theme!r} role keys diverge: "
        f"missing={set(_ROLE_KEYS) - set(block)}, "
        f"extra={set(block) - set(_ROLE_KEYS)}"
    )


# ── Color hex format ─────────────────────────────────────────────────

_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


@pytest.mark.parametrize("theme", _THEMES)
def test_surface_values_are_hex(theme):
    for key in _SURFACE_KEYS:
        val = T(theme=theme).surface(key)
        assert _HEX_RE.match(val), f"{theme}.surface.{key} = {val!r} not 6-digit hex"


@pytest.mark.parametrize("theme", _THEMES)
def test_role_values_are_hex(theme):
    for key in _ROLE_KEYS:
        val = T(theme=theme).role(key)
        assert _HEX_RE.match(val), f"{theme}.role.{key} = {val!r} not 6-digit hex"


# ── Typography ───────────────────────────────────────────────────────

def test_type_scale_monotonic_descending():
    """Type sizes must descend monotonically display > title > ... > micro.

    Locks the design intent: the order is the hierarchy, not the
    alphabetical key order.
    """
    ordered = [T.type(k) for k in _TYPE_KEYS if k != "code"]  # code is special
    assert ordered == sorted(ordered, reverse=True), (
        f"Type scale not monotonic descending (excluding 'code'): {ordered}"
    )


def test_type_keys_complete():
    for key in _TYPE_KEYS:
        size = T.type(key)
        assert isinstance(size, int) and 12 <= size <= 80


# ── Spacing ──────────────────────────────────────────────────────────

def test_space_scale_monotonic_ascending():
    ordered = [T.space(k) for k in _SPACE_KEYS]
    assert ordered == sorted(ordered), (
        f"Space scale not monotonic ascending: {ordered}"
    )


def test_space_values_positive():
    for key in _SPACE_KEYS:
        v = T.space(key)
        assert isinstance(v, float) and 0.0 < v < 5.0


# ── Stroke widths ────────────────────────────────────────────────────

def test_stroke_width_ascending():
    ordered = [T.stroke_width(k) for k in _STROKE_KEYS]
    assert ordered == sorted(ordered)


# ── Motion ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", _MOTION_KEYS)
def test_motion_spec_shape(key):
    spec = T.motion(key)
    assert "run_time" in spec and "rate_func" in spec
    assert isinstance(spec["run_time"], float) and 0.1 < spec["run_time"] < 5.0
    # rate_func is a string at this layer — components/moves resolve it.
    assert isinstance(spec["rate_func"], str) and spec["rate_func"]


def test_motion_returns_fresh_dict():
    """Caller must be free to mutate without poisoning the module-level table."""
    a = T.motion("emphasis")
    a["run_time"] = 999.0
    b = T.motion("emphasis")
    assert b["run_time"] != 999.0


def test_motion_run_times_ascending():
    """snap < emphasis < settle < grand. The name should match the feel."""
    times = [T.motion(k)["run_time"] for k in _MOTION_KEYS]
    assert times == sorted(times)


# ── Lag ──────────────────────────────────────────────────────────────

def test_lag_values_in_range():
    for k in _LAG_KEYS:
        v = T.lag(k)
        assert 0.0 < v < 1.0


# ── Shorthand properties ─────────────────────────────────────────────

@pytest.mark.parametrize("theme", _THEMES)
def test_bg_shorthand(theme):
    t = T(theme=theme)
    assert t.bg == t.surface("bg")


@pytest.mark.parametrize("theme", _THEMES)
def test_body_shorthand(theme):
    t = T(theme=theme)
    assert t.body == t.role("body")


# ── Errors ───────────────────────────────────────────────────────────

def test_invalid_theme_raises():
    with pytest.raises(ValueError, match="Unknown theme"):
        T(theme="cyberpunk")  # type: ignore[arg-type]


def test_invalid_surface_key_raises():
    with pytest.raises(KeyError, match="Unknown surface key"):
        T().surface("nope")


def test_invalid_role_key_raises():
    with pytest.raises(KeyError, match="Unknown role key"):
        T().role("nope")


def test_invalid_type_key_raises():
    with pytest.raises(KeyError, match="Unknown type key"):
        T.type("nope")


def test_invalid_space_key_raises():
    with pytest.raises(KeyError, match="Unknown space key"):
        T.space("nope")


def test_invalid_stroke_width_key_raises():
    with pytest.raises(KeyError, match="Unknown stroke_width key"):
        T.stroke_width("nope")


def test_invalid_motion_key_raises():
    with pytest.raises(KeyError, match="Unknown motion key"):
        T.motion("nope")


def test_invalid_lag_key_raises():
    with pytest.raises(KeyError, match="Unknown lag key"):
        T.lag("nope")


# ── Prompt block ─────────────────────────────────────────────────────

@pytest.mark.parametrize("theme", _THEMES)
def test_prompt_block_contains_token_names(theme):
    """The prompt-injection block must name every token role so the
    model has the full vocabulary, not a subset.
    """
    block = render_prompt_block(theme)
    for key in _ROLE_KEYS:
        assert key in block, f"role {key!r} missing from {theme} prompt block"
    for key in _SURFACE_KEYS:
        assert key in block, f"surface {key!r} missing from {theme} prompt block"
    for key in _TYPE_KEYS:
        assert key in block, f"type {key!r} missing from {theme} prompt block"
    for key in _MOTION_KEYS:
        assert key in block, f"motion {key!r} missing from {theme} prompt block"


@pytest.mark.parametrize("theme", _THEMES)
def test_prompt_block_contains_hex_values(theme):
    """Hex values for the bound theme appear in the prompt so the model
    can ground its choices when the agent reasons about color.
    """
    block = render_prompt_block(theme)
    bg = T(theme=theme).surface("bg")
    assert bg in block, f"surface bg hex {bg!r} missing from {theme} prompt block"


def test_prompt_block_forbids_raw_color_literals():
    """The prompt must explicitly tell the agent not to write raw hex /
    Manim color constants in scene code.
    """
    block = render_prompt_block("chalkboard")
    assert "NOT permitted" in block or "are not permitted" in block.lower() \
        or "forbidden" in block.lower(), (
        "Prompt block must forbid raw color literals so the agent stops "
        "regressing to hex hard-codes."
    )


def test_prompt_block_default_theme():
    """No-arg render_prompt_block defaults to chalkboard."""
    assert render_prompt_block() == render_prompt_block("chalkboard")


# ── Integration with manim_agent.THEME_SPECS ─────────────────────────

def test_manim_agent_theme_specs_derived_from_tokens():
    """THEME_SPECS in manim_agent must be the token-driven prompt
    blocks — guards against a future refactor that re-introduces flat
    hex palettes.
    """
    from pipeline.agents.manim_agent import THEME_SPECS

    for theme in _THEMES:
        assert THEME_SPECS[theme] == render_prompt_block(theme)
