# pipeline/ast_guards.py
"""Deterministic AST-level checks for the generated Manim scenes.

Runs as a stage between code_validator's existing Mobject-arithmetic guard and the
Claude semantic review. Each guard is a pure function over (tree, source) that
returns either a feedback string (single sentence prefixed with 'line N:') or None.

run_guards() runs every guard, aggregates non-None returns into a numbered list,
and returns the joined string (or None when all pass).
"""
from __future__ import annotations

import ast
import re


# ── Public ───────────────────────────────────────────────────────────────────

def run_guards(tree: ast.AST, source: str = "") -> str | None:
    """Run all guards. Returns aggregated feedback string, or None on clean code.

    Order is fixed (structural checks first, then heuristic) but the aggregated
    output is one numbered list — ordering only affects what number each
    violation shows up as.
    """
    violations: list[str] = []

    for guard in _ALL_GUARDS:
        feedback = guard(tree, source)
        if feedback is not None:
            violations.append(feedback)

    if not violations:
        return None

    body = "\n\n".join(f"{i+1}. {v}" for i, v in enumerate(violations))
    n = len(violations)
    plural = "issue" if n == 1 else "issues"
    return f"Found {n} AST-level {plural} to fix in the generated scene:\n\n{body}"


# ── Guards ───────────────────────────────────────────────────────────────────
# Tier A — pure structural (zero false positives expected):
#   _check_wait_literals
#   _check_scene_base_inheritance
#   _check_scene_base_import
#   _check_code_kwarg
#   _check_begin_segment
#   _check_end_layout_check
#
# Tier B — heuristic (rare false positives possible):
#   _check_code_attr_access
#   _check_next_to_chain_depth
#   _check_seg_data_loaded

def _check_wait_literals(tree: ast.AST, source: str = "") -> str | None:
    """Detect self.wait(<numeric literal>). Correct form uses _d[i] from segments.json."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # match self.wait(...)
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "self"
            and func.attr == "wait"
        ):
            continue
        # need at least one positional arg
        if not node.args:
            continue
        arg = node.args[0]
        if not isinstance(arg, ast.Constant):
            continue
        # tolerate bool — it's a Python int subclass but not the documented pattern
        if isinstance(arg.value, bool):
            continue
        if isinstance(arg.value, (int, float)):
            line_no = getattr(node, "lineno", "?")
            return (
                f"line {line_no}: self.wait({arg.value}) uses a hardcoded numeric "
                f"literal. Use _d[i] instead, where i is the segment index — e.g. "
                f"self.wait(_d[3]). The _d list comes from segments.json; loaded at "
                f"the top of construct() as `_seg_data = json.loads(...) ; _d = "
                f"[s[\"actual_duration_sec\"] for s in _seg_data]`."
            )
    return None


def _check_scene_base_inheritance(tree: ast.AST, source: str = "") -> str | None:
    """Detect class ChalkboardScene whose bases don't include ChalkboardSceneBase."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name != "ChalkboardScene":
            continue
        base_names = []
        for b in node.bases:
            if isinstance(b, ast.Name):
                base_names.append(b.id)
            elif isinstance(b, ast.Attribute):
                base_names.append(b.attr)
        if "ChalkboardSceneBase" in base_names:
            return None
        line_no = getattr(node, "lineno", "?")
        return (
            f"line {line_no}: class ChalkboardScene must inherit from "
            f"ChalkboardSceneBase. Change to "
            f"`class ChalkboardScene(ChalkboardSceneBase, Scene):`. The mixin "
            f"provides begin_segment/end_layout_check so the dry-run layout "
            f"check can validate timing and bounding boxes."
        )
    return None


def _check_scene_base_import(tree: ast.AST, source: str = "") -> str | None:
    """Detect missing `from chalkboard_base import ChalkboardSceneBase` at module level."""
    if not isinstance(tree, ast.Module):
        return None
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module != "chalkboard_base":
            continue
        for alias in node.names:
            if alias.name == "ChalkboardSceneBase":
                return None
    return (
        "module-level: missing `from chalkboard_base import ChalkboardSceneBase` "
        "at the top of scene.py. Add it as the first or near-first import. The "
        "ChalkboardSceneBase mixin is mounted by the renderer at /render/"
        "chalkboard_base.py."
    )


def _check_code_kwarg(tree: ast.AST, source: str = "") -> str | None:
    """Detect Code(code=...) — wrong kwarg in Manim v0.20.1 (correct is code_string=)."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_code = (
            (isinstance(func, ast.Name) and func.id == "Code")
            or (isinstance(func, ast.Attribute) and func.attr == "Code")
        )
        if not is_code:
            continue
        for kw in node.keywords:
            if kw.arg == "code":
                line_no = getattr(node, "lineno", "?")
                return (
                    f"line {line_no}: Code(code=...) uses the wrong keyword. "
                    f"Manim v0.20.1 expects `code_string=`. Change to "
                    f"`Code(code_string=\"...\", language=\"python\", "
                    f"background=\"window\", paragraph_config={{\"font_size\": N}})`."
                )
    return None


_SEGMENT_COMMENT_RE = re.compile(r"#\s*──\s*Segment\s+(\d+)\s*:")


def _check_begin_segment(tree: ast.AST, source: str = "") -> str | None:
    """Detect '# ── Segment N:' comments not followed by self.begin_segment(N, ...)
    within 3 lines.
    """
    if not source:
        return None

    # Build (line_no, N) tuples from source comments
    comment_segments: list[tuple[int, int]] = []
    for line_no, line in enumerate(source.splitlines(), start=1):
        m = _SEGMENT_COMMENT_RE.search(line)
        if m:
            comment_segments.append((line_no, int(m.group(1))))

    if not comment_segments:
        return None

    # Build a map of segment_index_int -> list of begin_segment line numbers
    begin_calls_by_n: dict[int, list[int]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "self"
            and func.attr in ("begin_segment", "next_segment")
        ):
            continue
        if not node.args:
            continue
        arg0 = node.args[0]
        if not (isinstance(arg0, ast.Constant) and isinstance(arg0.value, int) and not isinstance(arg0.value, bool)):
            continue
        n = arg0.value
        begin_calls_by_n.setdefault(n, []).append(getattr(node, "lineno", -1))

    for comment_line, n in comment_segments:
        candidates = begin_calls_by_n.get(n, [])
        if not any(comment_line <= cl <= comment_line + 3 for cl in candidates):
            return (
                f"line {comment_line}: missing `self.begin_segment({n}, "
                f"duration=_d[{n}])` (or `self.next_segment({n}, duration=_d[{n}], "
                f"clear=seg_items)`) within 3 lines of `# ── Segment {n}:`. "
                f"Every segment must start with begin_segment(N, duration=_d[N]) "
                f"so ChalkboardSceneBase can track timing for the dry-run layout "
                f"check."
            )

    return None


def _check_end_layout_check(tree: ast.AST, source: str = "") -> str | None:
    """Detect missing or out-of-order self.end_layout_check() in construct()."""
    construct_func: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "ChalkboardScene":
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == "construct":
                    construct_func = child
                    break
        if construct_func:
            break

    if construct_func is None:
        return None

    end_lc_lines: list[int] = []
    teardown_lines: list[int] = []

    for node in ast.walk(construct_func):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "self"):
            continue
        if func.attr == "end_layout_check":
            end_lc_lines.append(getattr(node, "lineno", -1))
            continue
        if func.attr == "play":
            for arg in node.args:
                if not isinstance(arg, ast.Starred):
                    continue
                inner = arg.value
                if isinstance(inner, ast.ListComp):
                    elt = inner.elt
                    if (
                        isinstance(elt, ast.Call)
                        and isinstance(elt.func, ast.Name)
                        and elt.func.id == "FadeOut"
                    ):
                        teardown_lines.append(getattr(node, "lineno", -1))

    # If there's no teardown FadeOut, this guard's premise doesn't apply — the
    # scene is trivial (e.g. just `pass`) or hasn't reached its final cleanup.
    # Return None to avoid firing on the canonical _OK_SCAFFOLD test case.
    if not teardown_lines:
        return None

    if not end_lc_lines:
        line_no = teardown_lines[-1]
        return (
            f"line {line_no}: self.end_layout_check() is missing from construct(). "
            f"It must appear before the final teardown "
            f"`self.play(*[FadeOut(m) for m in self.mobjects], ...)` so "
            f"ChalkboardSceneBase can finalize the layout report."
        )

    last_teardown = max(teardown_lines)
    last_end_lc = max(end_lc_lines)
    if last_end_lc > last_teardown:
        return (
            f"line {last_end_lc}: self.end_layout_check() appears AFTER the "
            f"final teardown `self.play(*[FadeOut(m) for m in self.mobjects], "
            f"...)` (line {last_teardown}). It must appear BEFORE the teardown "
            f"so ChalkboardSceneBase can finalize the layout report. Other "
            f"lines (e.g. self.wait(...)) between end_layout_check and the "
            f"teardown are fine — order matters, immediacy doesn't."
        )

    return None


def _check_code_attr_access(tree: ast.AST, source: str = "") -> str | None:
    """Detect `<var>.code` access on a variable assigned from Code(...).

    Heuristic — only catches direct assignments (`v = Code(...)`); doesn't
    track aliasing or function returns.
    """
    code_vars: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            value = node.value
        else:
            continue
        if value is None:
            continue
        if not (
            isinstance(value, ast.Call)
            and (
                (isinstance(value.func, ast.Name) and value.func.id == "Code")
                or (isinstance(value.func, ast.Attribute) and value.func.attr == "Code")
            )
        ):
            continue
        if isinstance(node, ast.Assign):
            targets = node.targets
        else:
            targets = [node.target] if node.target is not None else []
        for tgt in targets:
            if isinstance(tgt, ast.Name):
                code_vars.add(tgt.id)

    if not code_vars:
        return None

    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if node.attr != "code":
            continue
        if not isinstance(node.value, ast.Name):
            continue
        if node.value.id not in code_vars:
            continue
        line_no = getattr(node, "lineno", "?")
        return (
            f"line {line_no}: {node.value.id}.code does not exist on Manim "
            f"v0.20.1's Code object. Use {node.value.id}.code_lines[i] "
            f"(zero-indexed VGroup) to access the i-th line instead."
        )

    return None


def _check_next_to_chain_depth(tree: ast.AST, source: str = "") -> str | None:
    """Detect chains of `.next_to(...).next_to(...).next_to(...)` (depth > 2)."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        depth = 0
        cur = node
        while True:
            if not isinstance(cur, ast.Call):
                break
            if not (isinstance(cur.func, ast.Attribute) and cur.func.attr == "next_to"):
                break
            depth += 1
            cur = cur.func.value
        if depth > 2:
            line_no = getattr(node, "lineno", "?")
            return (
                f"line {line_no}: .next_to() chain depth is {depth} (limit is 2). "
                f"Drift compounds across chained next_to() calls. Use absolute "
                f"positioning via .move_to(np.array([x, y, z])) for the third "
                f"element instead, or shift relative to the original anchor."
            )
    return None


def _check_seg_data_loaded(tree: ast.AST, source: str = "") -> str | None:
    """Detect _d or _seg_data referenced without a matching load from segments.json."""
    refs: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id in ("_d", "_seg_data"):
                refs.append(getattr(node, "lineno", -1))

    if not refs:
        return None

    has_load = False
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign) or isinstance(node, ast.AnnAssign)):
            continue
        value = node.value if isinstance(node, ast.Assign) else node.value
        if value is None:
            continue
        for sub in ast.walk(value):
            if isinstance(sub, ast.Constant) and sub.value == "segments.json":
                has_load = True
                break
        if has_load:
            break

    if has_load:
        return None

    first_ref = min(refs) if refs else "?"
    return (
        f"line {first_ref}: _seg_data / _d is referenced but never loaded "
        f"from segments.json. Add at the top of construct(): "
        f"`_seg_data = json.loads((Path(__file__).parent / \"segments.json\")"
        f".read_text()); _d = [s[\"actual_duration_sec\"] for s in _seg_data]`."
    )


def _extract_constant_number(node: ast.AST) -> float | None:
    """Reduce a small AST expression to a Python number, or None if we can't.
    Handles ast.Constant, ast.UnaryOp(USub), and the trivial ast.BinOp shapes
    that Manim layout code uses (`-4.5`, `7.0 / 2`, etc.). Anything more
    exotic — variables, function calls, attribute access — gives up.
    """
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            return None
        if isinstance(node.value, (int, float)):
            return float(node.value)
        return None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _extract_constant_number(node.operand)
        return -inner if inner is not None else None
    if isinstance(node, ast.BinOp):
        left = _extract_constant_number(node.left)
        right = _extract_constant_number(node.right)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div) and right != 0:
            return left / right
    return None


def _extract_x0_from_offset_expr(expr: ast.AST, loop_var: str) -> float | None:
    """Best-effort: parse an expression like `-4.5 + i * 0.85` (where `i` is
    the loop variable) into the constant `x_0`. Looks for a top-level Add
    with one side reducible to a constant and the other side referencing
    the loop variable. Anything more complex returns None.

    Recognized shapes (commutative): `c + i * w`, `c + i * w + k` (folded),
    `c - i * w`. The exact step coefficient `w` doesn't matter for the
    overflow heuristic — we only need x_0 (the leftmost cell's center).
    """
    if not isinstance(expr, ast.BinOp):
        return None
    if not isinstance(expr.op, (ast.Add, ast.Sub)):
        return None

    def _references_var(n: ast.AST, var: str) -> bool:
        for sub in ast.walk(n):
            if isinstance(sub, ast.Name) and sub.id == var:
                return True
        return False

    left_uses_var = _references_var(expr.left, loop_var)
    right_uses_var = _references_var(expr.right, loop_var)
    # Ambiguous (both sides reference i, or neither does) → give up.
    if left_uses_var == right_uses_var:
        return None

    constant_side = expr.right if left_uses_var else expr.left
    return _extract_constant_number(constant_side)


def _check_horizontal_array_zone_overflow(tree: ast.AST, source: str = "") -> str | None:
    """Detect horizontal arrays whose right edge crosses past the LEFT-zone
    boundary (x = -0.5). Pattern:

        for i in range(N):
            cell = RoundedRectangle(width=W, ...)
            cell.move_to(np.array([-4.5 + i * W, 0, 0]))
            ...

    We extract:
      - N from `range(N)` (constant int)
      - W from any constructor call with a numeric `width=` kwarg in the loop
      - x_0 from the offset expression (best-effort, see _extract_x0_from_offset_expr)
    Then compute right_edge = x_0 + (N - 0.5) * W. If > -0.5, flag.

    This is a heuristic; many shapes won't match cleanly and fall through to
    None. Better to under-fire than to false-positive on the canonical
    _OK_SCAFFOLD test case in the test suite.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue
        # `for <name> in range(<int_literal>):`
        if not isinstance(node.target, ast.Name):
            continue
        loop_var = node.target.id
        if not (
            isinstance(node.iter, ast.Call)
            and isinstance(node.iter.func, ast.Name)
            and node.iter.func.id == "range"
            and len(node.iter.args) >= 1
        ):
            continue
        # Take the LAST positional arg as the upper bound of range — works for
        # range(N), range(start, N), and range(start, N, step). Cheap heuristic.
        n_arg = node.iter.args[-1]
        n = _extract_constant_number(n_arg)
        if n is None:
            continue
        n_int = int(n)
        if n_int <= 0:
            continue

        # Walk the loop body for:
        #   <var>.move_to(np.array([<expr referencing i>, ...]))
        # AND for any constructor call with width=<const> kwarg.
        widths: list[float] = []
        x0_candidates: list[float] = []
        for body_node in ast.walk(node):
            if not isinstance(body_node, ast.Call):
                continue
            # width=<numeric constant> kwarg
            for kw in body_node.keywords:
                if kw.arg == "width":
                    w = _extract_constant_number(kw.value)
                    if w is not None and w > 0:
                        widths.append(w)
            # .move_to(np.array([<expr>, ...])) — works whether the call is
            # bound to a variable assignment in the loop or chained off a
            # constructor result.
            func = body_node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "move_to"):
                continue
            if not body_node.args:
                continue
            arg0 = body_node.args[0]
            # Look for np.array([...]) — accept either np.array or array shapes.
            list_node: ast.List | None = None
            if (
                isinstance(arg0, ast.Call)
                and isinstance(arg0.func, ast.Attribute)
                and arg0.func.attr == "array"
                and arg0.args
                and isinstance(arg0.args[0], ast.List)
            ):
                list_node = arg0.args[0]
            elif isinstance(arg0, ast.List):
                # Bare list — Manim accepts both
                list_node = arg0
            if list_node is None or not list_node.elts:
                continue
            x_expr = list_node.elts[0]
            x0 = _extract_x0_from_offset_expr(x_expr, loop_var)
            if x0 is not None:
                x0_candidates.append(x0)

        if not widths or not x0_candidates:
            continue

        # Use the smallest x_0 (most-negative left edge) and the largest
        # width as the worst-case combination. If the worst case still
        # right-edges below -0.5, the layout is safe.
        x_0 = min(x0_candidates)
        w_max = max(widths)
        right_edge = x_0 + (n_int - 0.5) * w_max
        if right_edge > -0.5:
            line_no = getattr(node, "lineno", "?")
            return (
                f"line {line_no}: horizontal array `for {loop_var} in range({n_int}):` "
                f"with leftmost center x_0={x_0:.2f} and cell width W={w_max:.2f} "
                f"places its right edge at x_0 + (N − 0.5) × W = "
                f"{right_edge:.2f}, crossing into the RIGHT zone "
                f"(must be < -0.5 for LEFT-zone content). Reduce N, reduce W, "
                f"or shift x_0 further left. With x_0 = -3.5 the LEFT zone "
                f"can hold roughly 3 units of total horizontal width "
                f"(e.g. 3 cols × W=0.9 or 2 cols × W=1.4)."
            )
    return None


# ── Phase 5 — design-system enforcement ──────────────────────────────────────
#
# Phase 4 (#335) taught the manim_agent the design-system vocabulary —
# components, moves, tokens. Phase 5 puts deterministic teeth behind the
# prompt's FORBIDDEN list so the agent can't quietly regress.
#
# Tier A2 — design-system structural enforcement (zero FP):
#   _check_no_raw_hex_in_color_kwargs    — forbid "#RRGGBB" in color= / fill_color= / etc.
#   _check_no_manim_color_constants      — forbid `RED`, `BLUE`, etc. as color values
#   _check_design_system_imports         — require tokens + components + moves imports
#                                          when the scene has at least one begin_segment
#                                          (i.e., it's a real scene, not a `pass` scaffold)
#
# Tier B2 — design-system heuristic (rare FP):
#   _check_no_raw_primitive_construction — forbid Square / RoundedRectangle / Code /
#                                          Arrow / NumberLine where a component covers
#                                          the use case unambiguously

# Color kwargs that components and Manim primitives both accept. The hex
# guard fires only when one of these is the kwarg name — avoids false
# positives on hex strings inside code_string= (ChalkCode source) or
# other text-content kwargs.
_COLOR_KWARGS = frozenset({
    "color",
    "fill_color",
    "stroke_color",
    "background_color",
    "stroke",
    "fill",
    "set_color",
    "set_fill",
    "set_stroke",
})

# Color attributes assignable via `self.camera.background_color = "#..."`
# or similar. Same pattern, different AST shape.
_COLOR_ATTRS = frozenset({
    "background_color",
    "color",
    "fill_color",
    "stroke_color",
})

# Hex color literal pattern. Matches "#RGB", "#RRGGBB", and "#RRGGBBAA"
# (alpha variant). Doesn't match arbitrary strings starting with # — the
# trailing length constraint keeps comments / paths / random strings out.
_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{3}([0-9A-Fa-f]{3})?([0-9A-Fa-f]{2})?$")


def _check_no_raw_hex_in_color_kwargs(tree: ast.AST, source: str = "") -> str | None:
    """Forbid raw hex string literals used as color values.

    Fires when a string matching the hex pattern (#RGB / #RRGGBB / with
    optional alpha) appears as:
      * value of a kwarg in _COLOR_KWARGS  (e.g. color="#FF0000")
      * positional arg to ManimColor(...)
      * RHS of an assignment to a `.background_color` / `.color` /
        `.fill_color` / `.stroke_color` attribute

    Why this is Tier A: any hex literal in these contexts is a regression
    from the design system. Roles + surface keys are the canonical
    source.
    """
    for node in ast.walk(tree):
        # Pattern 1: color= kwarg
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg in _COLOR_KWARGS and _is_hex_string_constant(kw.value):
                    return _hex_feedback(
                        kw.value.value, getattr(node, "lineno", "?"),
                        context=f"{kw.arg}=",
                    )
            # Pattern 2: ManimColor("#...")
            func_id = _call_func_name(node)
            if func_id == "ManimColor" and node.args:
                a0 = node.args[0]
                if _is_hex_string_constant(a0):
                    return _hex_feedback(
                        a0.value, getattr(node, "lineno", "?"),
                        context="ManimColor(...)",
                    )
        # Pattern 3: assignment to a `.background_color` / etc. attribute
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Attribute) and tgt.attr in _COLOR_ATTRS:
                    if _is_hex_string_constant(node.value):
                        return _hex_feedback(
                            node.value.value, getattr(node, "lineno", "?"),
                            context=f"<obj>.{tgt.attr} =",
                        )
    return None


def _is_hex_string_constant(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and bool(_HEX_COLOR_RE.match(node.value))
    )


def _call_func_name(call: ast.Call) -> str | None:
    """Top-level callable name for a Call node. None for chained attrs."""
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _hex_feedback(value: str, line_no, *, context: str) -> str:
    return (
        f"line {line_no}: raw hex color literal {value!r} used as {context}. "
        f"Design-system enforcement (Phase 5): no scene-level hex literals. "
        f"Replace with `t.role('focus_primary')` / `t.role('body')` / etc. "
        f"for foreground colors, or `t.surface('bg')` / `t.surface('bg_subtle')` "
        f"for backgrounds. Bind tokens once at the top of construct(): "
        f"`t = T(theme=\"chalkboard\")`."
    )


# Manim's built-in color constants. Used in scene code as `color=RED`,
# `fill_color=BLUE`, etc. Forbidden in scene code (the design system
# carries semantic meaning that named constants don't). Generated by
# enumerating the base set + the A-E intensity variants Manim exposes.
def _build_manim_color_constants() -> frozenset[str]:
    base = {
        "WHITE", "BLACK", "GRAY", "GREY",
        "RED", "ORANGE", "YELLOW", "GREEN", "BLUE", "PURPLE",
        "PINK", "BROWN", "MAROON", "TEAL", "GOLD", "AQUA",
        "GREEN_SCREEN", "BLUE_SCREEN",
        "PURE_RED", "PURE_GREEN", "PURE_BLUE",
        "DARK_GRAY", "LIGHT_GRAY", "DARK_GREY", "LIGHT_GREY",
        "DARK_BROWN", "LIGHT_BROWN", "LIGHT_PINK",
    }
    # A-E intensity variants for the chromatic + grayscale base set
    for color in (
        "WHITE", "GRAY", "GREY", "RED", "ORANGE", "YELLOW", "GREEN",
        "BLUE", "PURPLE", "PINK", "TEAL", "GOLD", "MAROON", "AQUA",
    ):
        for v in "ABCDE":
            base.add(f"{color}_{v}")
    return frozenset(base)


_MANIM_COLOR_CONSTANTS = _build_manim_color_constants()


def _check_no_manim_color_constants(tree: ast.AST, source: str = "") -> str | None:
    """Forbid Manim's built-in color constants (RED, BLUE_E, etc.) used
    as color values in scene code.

    Fires when a Name node in `_MANIM_COLOR_CONSTANTS` appears as:
      * value of a kwarg in _COLOR_KWARGS  (e.g. color=RED)
      * positional arg to ManimColor / set_color / set_fill / set_stroke
      * RHS of an assignment to a `.color`-family attribute

    Why this is Tier A: the constants are unambiguously colors when
    used in these contexts. The scene SHOULD say `t.role('focus_primary')`
    so the choice carries pedagogical meaning across theme swaps.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg in _COLOR_KWARGS and _is_manim_color_name(kw.value):
                    return _const_feedback(
                        kw.value.id, getattr(node, "lineno", "?"),
                        context=f"{kw.arg}=",
                    )
            # set_color / set_fill / set_stroke positional arg
            method = _call_func_name(node)
            if method in {"set_color", "set_fill", "set_stroke", "ManimColor"}:
                if node.args and _is_manim_color_name(node.args[0]):
                    return _const_feedback(
                        node.args[0].id, getattr(node, "lineno", "?"),
                        context=f"{method}(...)",
                    )
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Attribute) and tgt.attr in _COLOR_ATTRS:
                    if _is_manim_color_name(node.value):
                        return _const_feedback(
                            node.value.id, getattr(node, "lineno", "?"),
                            context=f"<obj>.{tgt.attr} =",
                        )
    return None


def _is_manim_color_name(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id in _MANIM_COLOR_CONSTANTS


def _const_feedback(name: str, line_no, *, context: str) -> str:
    return (
        f"line {line_no}: Manim color constant `{name}` used as {context}. "
        f"Design-system enforcement (Phase 5): scene code references colors "
        f"by SEMANTIC ROLE, not by named Manim constant. Replace with "
        f"`t.role('focus_primary')` (the magnet element), "
        f"`t.role('focus_secondary')` (paired/tracked element), "
        f"`t.role('context_muted')` (past/dimmed), etc. The role name carries "
        f"pedagogical meaning that survives theme swaps; `RED` does not."
    )


# Manim primitives whose use is fully covered by a Phase-2 component.
# Conservative list — only includes primitives where the component
# replacement is unambiguous (Code is always source, Arrow is always
# directional, NumberLine/Axes are always scales, Matrix is always a
# matrix). Square, Rectangle and Circle are deliberately omitted: they
# have legitimate non-replaceable uses (frames, geometric proofs such as
# the squares on a right triangle's sides).
_REPLACED_PRIMITIVES: dict[str, str] = {
    "RoundedRectangle": "ChalkBox (or ChalkPanel for section frames)",
    "Code":             "ChalkCode",
    "Arrow":            "ChalkArrow",
    "CurvedArrow":      "ChalkArrow (with curve=True)",
    "NumberLine":       "ChalkAxis",
    "Axes":             "ChalkAxes",
    "Matrix":           "ChalkMatrix",
}


def _check_no_raw_primitive_construction(tree: ast.AST, source: str = "") -> str | None:
    """Forbid direct construction of primitives that have a component
    replacement.

    Fires when a Call node's callable resolves (heuristically — bare
    Name or Attribute.attr) to a name in `_REPLACED_PRIMITIVES`. Skips
    cases inside class definitions of *components themselves* (e.g.,
    chalkboard_components.py constructs RoundedRectangle internally —
    that's its job). The guard runs on generated scene code, which by
    convention is the ChalkboardScene class, so the heuristic is: only
    fire when the call is reachable from a class named ChalkboardScene
    or from module-level code (helper functions in the scene file).
    """
    # Build a set of class names whose bodies are exempt from this guard.
    # Component classes (anything that inherits from VGroup directly or
    # appears to be a component) — heuristic. In practice the agent only
    # writes ChalkboardScene, so this is a safety net.
    exempt_class_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            base_names = []
            for b in node.bases:
                if isinstance(b, ast.Name):
                    base_names.append(b.id)
                elif isinstance(b, ast.Attribute):
                    base_names.append(b.attr)
            # If a class subclasses VGroup directly (and isn't
            # ChalkboardScene), treat it as a custom component the agent
            # is defining inline — out of scope for this guard.
            if "VGroup" in base_names and node.name != "ChalkboardScene":
                start = node.lineno
                end = node.end_lineno or start + 1000
                exempt_class_lines.update(range(start, end + 1))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_func_name(node)
        if name not in _REPLACED_PRIMITIVES:
            continue
        line_no = getattr(node, "lineno", 0)
        if line_no in exempt_class_lines:
            continue
        return (
            f"line {line_no}: direct construction of `{name}(...)`. "
            f"Design-system enforcement (Phase 5): scene code uses "
            f"components instead. Replace `{name}(...)` with "
            f"`{_REPLACED_PRIMITIVES[name]}(...)`. The component sets "
            f"token-driven defaults (stroke, fill, font, layout) and "
            f"exposes .highlight(role) / .mute() methods so the moves "
            f"library can apply semantic emphasis without knowing each "
            f"primitive's internal structure."
        )
    return None


def _check_design_system_imports(tree: ast.AST, source: str = "") -> str | None:
    """Require the three design-system imports when the scene is real
    (i.e., has at least one self.begin_segment(...) call).

    The empty `pass`-bodied scaffold used by test fixtures has no
    begin_segment, so this guard doesn't fire on it. Any generated scene
    with actual segments must `from chalkboard_tokens import T` AND
    `from chalkboard_components import ...` AND `from chalkboard_moves
    import ...`. Star imports satisfy the requirement too.
    """
    # Determine if the scene is "real" — has at least one begin_segment.
    has_segments = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "self"
            and func.attr in ("begin_segment", "next_segment")
        ):
            has_segments = True
            break
    if not has_segments:
        return None

    # Collect imports.
    has_tokens = False
    has_components = False
    has_moves = False
    if isinstance(tree, ast.Module):
        for node in tree.body:
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module == "chalkboard_tokens":
                has_tokens = True
            elif node.module == "chalkboard_components":
                has_components = True
            elif node.module == "chalkboard_moves":
                has_moves = True

    missing = []
    if not has_tokens:
        missing.append("`from chalkboard_tokens import T`")
    if not has_components:
        missing.append(
            "`from chalkboard_components import ChalkBox, ChalkArrow, "
            "ChalkCode, Callout, StepCounter, ChalkAxis, ChalkAxes, ChalkPanel, "
            "ChalkBadge, EquationGroup, ChalkMatrix, NetworkNode, math_tex, "
            "tex, resolve_motion`"
        )
    if not has_moves:
        missing.append(
            "`from chalkboard_moves import reveal_with_emphasis, "
            "compare_split, focus_zoom, morph_show_equivalence, "
            "cascade_reveal, progressive_step, annotate_and_pause, "
            "chapter_transition, derivation_step, transform_equation, "
            "emphasize_term`"
        )
    if not missing:
        return None
    bullets = "\n  - ".join(missing)
    return (
        f"module-level: missing design-system import(s):\n  - {bullets}\n"
        f"Design-system enforcement: every scene with "
        f"begin_segment() must import the three surfaces. Tokens drive "
        f"colors / sizes / motion; components replace raw primitives; "
        f"moves replace raw self.play(...) with named pedagogical "
        f"patterns. The required scaffold in the manim_agent system "
        f"prompt has the full import block."
    )


def _check_no_play_with_filtered_comp(tree: ast.AST, source: str = "") -> str | None:
    """Detect `self.play(*[expr for x in items if cond])` where the
    comprehension's `if`-clause can produce an empty list.

    Manim's `Scene.play(*animations)` raises
    `ValueError: Called Scene.play with no animations` when no
    animations are passed. The agent regularly writes patterns like:

        self.play(*[FadeOut(m) for m in seg_items if m not in boxes],
                  run_time=0.3)

    intending to fade out the difference between two sets. If the
    filter excludes everything (e.g., during a segment transition
    where seg_items happens to equal `boxes` exactly), the list is
    empty, the star-unpacks zero arguments, and the render crashes
    mid-scene.

    Render-time bug, not visible until the actual Manim job runs —
    the layout-checker dry-run won't catch this if its execution path
    doesn't hit the same filter-empty state.


    SAFE PATTERN (suggested in the feedback):

        to_fade = [FadeOut(m) for m in seg_items if m not in boxes]
        if to_fade:
            self.play(*to_fade, run_time=0.3)

    Tier B heuristic — fires on EVERY `self.play(*[<comp> if ...])`
    pattern, even when the agent can prove statically the filter is
    non-empty. False-positive rate is acceptable because the safe
    pattern is also more correct and readable.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "self"
            and func.attr == "play"
        ):
            continue
        # Look for any Starred positional arg with a ListComp value
        # whose generators include an `if`-clause.
        for arg in node.args:
            if not isinstance(arg, ast.Starred):
                continue
            comp = arg.value
            if not isinstance(comp, ast.ListComp):
                continue
            # Any `if`-clause in any generator means the resulting
            # list could be empty.
            has_if = any(gen.ifs for gen in comp.generators)
            if not has_if:
                continue
            line_no = getattr(node, "lineno", "?")
            return (
                f"line {line_no}: self.play(*[<comp> if <cond>]) — the "
                f"filter could exclude every element, in which case "
                f"`*[]` calls self.play() with zero animations and "
                f"Manim raises `ValueError: Called Scene.play with no "
                f"animations`. Render-time crash, not caught by the "
                f"layout-checker dry-run.\n\n"
                f"Fix: extract the list, guard with `if`:\n\n"
                f"    to_play = [<expr> for <x> in <items> if <cond>]\n"
                f"    if to_play:\n"
                f"        self.play(*to_play, run_time=...)\n\n"
                f"This is safe regardless of what `cond` evaluates to."
            )
    return None


def _check_no_growarrow(tree: ast.AST, source: str = "") -> str | None:
    """Forbid `GrowArrow(...)` in scene code (Tier B3 render-time crash).

    GrowArrow.__init__ calls `arrow.get_start()` on its argument to find the
    tail to grow from. But design-system arrows are `ChalkArrow`, a
    `_ChalkComponent` (VGroup) that wraps the real Arrow as a *submobject*
    (`self.add(arrow)`) — so the ChalkArrow itself has no points and
    get_start() raises:

        Exception: Cannot call Mobject.get_start for a Mobject with no points

    This crashes the render mid-scene; the
    layout-checker dry-run doesn't always hit it. Raw `Arrow` is already
    forbidden by `_check_no_raw_primitive_construction`, so GrowArrow has no
    valid target in scene code at all — forbid it outright and steer to
    `Create(...)`.

    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _call_func_name(node) != "GrowArrow":
            continue
        line_no = getattr(node, "lineno", "?")
        return (
            f"line {line_no}: GrowArrow(...) — GrowArrow calls .get_start() on "
            f"its argument, but a ChalkArrow is a VGroup that wraps the real "
            f"Arrow as a submobject and has no points of its own, so this "
            f"raises `Cannot call Mobject.get_start for a Mobject with no "
            f"points` and crashes the render mid-scene. Raw Arrow is forbidden "
            f"by the design system, so GrowArrow has no valid target. Reveal a "
            f"ChalkArrow with `Create(arrow)` (draws the stroke) or "
            f"`FadeIn(arrow)` instead."
        )
    return None


# ── Math typesetting ─────────────────────────────────────────────────────────
#
# Math rendered through Text (Pango) looks amateur: no italics for
# variables, no real fractions/superscripts, inconsistent spacing. Every
# formula must go through MathTex/Tex (math_tex / tex / EquationGroup).
# This guard flags Text-family calls whose literal string contains LaTeX
# syntax or Unicode math symbols. Arrows (→) and × are deliberately allowed:
# they are common in prose labels ("Input → Output", "3 × 3 grid").

_TEXT_CALLS = frozenset({"Text", "MarkupText", "Paragraph"})
_LATEX_IN_TEXT_RE = re.compile(
    r"\\[A-Za-z]+"          # LaTeX command: \frac, \int, \alpha
    r"|[A-Za-z0-9)\]]\^"     # caret exponent: x^2, (a+b)^n, e^x
    r"|_\{"                  # braced subscript: a_{n}
)
_UNICODE_MATH = set("²³¹⁰⁴⁵⁶⁷⁸⁹ⁿⁱ₀₁₂₃₄₅₆₇₈₉ₙ∫∬∮∑∏√∛∂∇∞≤≥≠≈≡∝±∓÷∈∉⊂⊆⊃∪∩∀∃⇒⇔⟹⟺")


def _string_literal_text(node: ast.AST) -> str | None:
    """Literal text of a str Constant or the constant parts of an f-string."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        # Interpolations stand in as "x" so "{v}^2" still reads as a power.
        return "".join(
            v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "x"
            for v in node.values
        )
    return None


def _check_no_math_in_text(tree: ast.AST, source: str = "") -> str | None:
    """Forbid math typeset with Text: Text("x^2"), Text("\\frac{a}{b}"),
    Text("√2 ≈ 1.414"). Use math_tex / tex / EquationGroup instead."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_func_name(node) not in _TEXT_CALLS:
            continue
        if not node.args:
            continue
        s = _string_literal_text(node.args[0])
        if not s:
            continue
        bad_chars = sorted({c for c in s if c in _UNICODE_MATH})
        m = _LATEX_IN_TEXT_RE.search(s)
        if not (m or bad_chars):
            continue
        line_no = getattr(node, "lineno", "?")
        what = f"LaTeX syntax {m.group(0)!r}" if m else f"math symbol(s) {''.join(bad_chars)!r}"
        return (
            f"line {line_no}: {_call_func_name(node)}({s[:40]!r}) contains {what}. "
            f"Math must be typeset with LaTeX, never Text: use "
            f"math_tex(r\"...\") for a formula (e.g. math_tex(r\"x^2 + 1\")), "
            f"tex(r\"words with $inline math$\") for prose containing math "
            f"(titles too: tex(r\"Why $\\frac{{\\dd}}{{\\dd x}} e^x = e^x$\", size=\"title\")), "
            f"or EquationGroup([...]) for multi-line derivations."
        )
    return None


# Registry of all guards in execution order. Populated as guards land in
# subsequent tasks; keep alphabetic-by-tier ordering for readability.
_ALL_GUARDS: list = [
    # Tier A
    _check_wait_literals,
    _check_scene_base_inheritance,
    _check_scene_base_import,
    _check_code_kwarg,
    _check_begin_segment,
    _check_end_layout_check,
    # Tier A2 — design-system structural (Phase 5)
    _check_no_raw_hex_in_color_kwargs,
    _check_no_manim_color_constants,
    _check_design_system_imports,
    # Tier B
    _check_code_attr_access,
    _check_next_to_chain_depth,
    _check_seg_data_loaded,
    # Tier B2 — design-system heuristic (Phase 5)
    _check_no_raw_primitive_construction,
    # Tier B3 — render-time crash patterns (smoke-test follow-ups)
    _check_no_play_with_filtered_comp,
    _check_no_growarrow,
    # Tier B4 — math typesetting
    _check_no_math_in_text,
    # Tier C — heuristic geometric checks
    _check_horizontal_array_zone_overflow,
]
