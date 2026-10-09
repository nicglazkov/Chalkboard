# pipeline/scene_parts.py
"""Chunked scene code: split the segments into parts, assemble part methods
into one ChalkboardScene, and route validator feedback back to the parts.

A wide topic can need more scene code (plus the thinking before it) than one
Claude response can hold. manim_agent then writes the scene in parts: one
method per group of consecutive segments,

    def part_K(self, t, _d, seg_items):
        # ── Segment N: ... ──
        self.next_segment(N, duration=_d[N], clear=seg_items)   # begin_segment for 0
        seg_items = []
        ...
        return seg_items

and this module assembles them into a normal scene (shared imports, duration
loading, theme binding, end_layout_check and the final FadeOut written once),
so the AST guards, code review and layout dry-run see one ordinary scene.
Nothing here calls Claude.
"""
from __future__ import annotations

import ast
import re
import textwrap

# Segments per part. Three segments of a dense lecture is about 15-25k tokens
# of scene code plus thinking, well inside one response.
SEGMENTS_PER_PART = 3

SCAFFOLD_IMPORTS = """from chalkboard_base import ChalkboardSceneBase
from chalkboard_tokens import T
from chalkboard_components import (
    ChalkBox, ChalkArrow, ChalkCode, Callout, StepCounter,
    ChalkAxis, ChalkAxes, ChalkPanel, ChalkBadge, EquationGroup,
    ChalkMatrix, NetworkNode, math_tex, tex, resolve_motion,
)
from chalkboard_moves import (
    reveal_with_emphasis, compare_split, focus_zoom,
    morph_show_equivalence, cascade_reveal, progressive_step,
    annotate_and_pause, chapter_transition,
    derivation_step, transform_equation, emphasize_term,
)
from chalkboard_templates import (
    AlgorithmTemplate, CodeTemplate, CompareTemplate,
    DerivationTemplate, HowtoTemplate, TimelineTemplate,
)
from manim import *
import json
from pathlib import Path"""


def part_ranges(n_segments: int, per_part: int = SEGMENTS_PER_PART) -> list[tuple[int, int]]:
    """Consecutive, balanced (first, last) segment ranges, inclusive."""
    if n_segments <= 0:
        return []
    n_parts = max(1, -(-n_segments // per_part))
    base, extra = divmod(n_segments, n_parts)
    out, start = [], 0
    for k in range(n_parts):
        size = base + (1 if k < extra else 0)
        out.append((start, start + size - 1))
        start += size
    return out


def method_name(k: int) -> str:
    return f"part_{k}"


class PartError(ValueError):
    """A part's code is not one usable method definition."""


def extract_method(code: str, k: int) -> str:
    """The source of `def part_k(self, t, _d, seg_items)` from a part response.

    Accepts the method alone (any indentation). Raises PartError with a
    message meant for the model when the code is not exactly that.
    """
    src = textwrap.dedent(code).strip("\n")
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        raise PartError(f"syntax error in part {k}: {e.msg} (line {e.lineno} of the part)") from e
    name = method_name(k)
    defs = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    others = [n for n in tree.body if not isinstance(n, (ast.FunctionDef, ast.Import, ast.ImportFrom))]
    if len(defs) != 1 or others:
        raise PartError(
            f"part {k} must be exactly one top-level method `def {name}(self, t, _d, seg_items):` "
            f"(helpers go inside it); got {len(defs)} function(s) and {len(others)} other statement(s)"
        )
    fn = defs[0]
    args = [a.arg for a in fn.args.args]
    if fn.name != name or args[:4] != ["self", "t", "_d", "seg_items"]:
        raise PartError(f"part {k} must be `def {name}(self, t, _d, seg_items):`, "
                        f"got `def {fn.name}({', '.join(args)})`")
    if not any(isinstance(n, ast.Return) for n in ast.walk(fn)):
        raise PartError(f"part {k} must end with `return seg_items`")
    lines = src.splitlines()
    start = fn.lineno - 1 - len(fn.decorator_list)
    return "\n".join(lines[start:fn.end_lineno])


def clean_imports(imports) -> list[str]:
    """Extra import lines a part asked for: valid import statements only,
    minus what the scaffold already imports."""
    out: list[str] = []
    scaffold = set(SCAFFOLD_IMPORTS.splitlines())
    for line in imports or []:
        if not isinstance(line, str):
            continue
        line = line.strip()
        if not line or line in scaffold or line in out:
            continue
        try:
            tree = ast.parse(line)
        except SyntaxError:
            continue
        if len(tree.body) == 1 and isinstance(tree.body[0], (ast.Import, ast.ImportFrom)):
            out.append(line)
    return out


def assemble(parts: list[dict], n_segments: int, theme: str) -> tuple[str, list[tuple[int, int]]]:
    """One scene from part dicts {"segments": [a, b], "code": str, "imports": [...]}.

    Returns (code, spans): spans[k] = (first_line, last_line), 1-based and
    inclusive, of part k's method in the assembled code.
    """
    extra: list[str] = []
    for p in parts:
        for line in clean_imports(p.get("imports")):
            if line not in extra:
                extra.append(line)
    head = SCAFFOLD_IMPORTS + ("\n" + "\n".join(extra) if extra else "")
    calls = "\n".join(f"        seg_items = self.{method_name(k)}(t, _d, seg_items)"
                      for k in range(len(parts)))
    construct = f'''

class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        _seg_data = json.loads((Path(__file__).parent / "segments.json").read_text())
        _d = [s["actual_duration_sec"] for s in _seg_data]
        _d = _d + [2.0] * max(0, {n_segments} - len(_d))

        t = T(theme="{theme}")
        self.camera.background_color = t.bg
        self.title_mob = None

        # The scene is written in {len(parts)} parts (one method per group of segments).
        seg_items = []
{calls}

        # ── End ──
        self.end_layout_check()
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.5)
'''
    code = head + construct
    spans: list[tuple[int, int]] = []
    for k, p in enumerate(parts):
        a, b = p["segments"]
        block = f"\n    # ── Part {k}: segments {a}-{b} ──\n" + textwrap.indent(p["code"].strip("\n"), "    ") + "\n"
        first = code.count("\n") + 3  # the def line, after the blank line and the part comment
        code += block
        spans.append((first, code.count("\n")))
    return code, spans


_LINE_RE = re.compile(r"\bline\s+(\d+)", re.IGNORECASE)
_SEGMENT_RE = re.compile(r"\bsegments?\s+(\d+)", re.IGNORECASE)


def route_feedback(feedback: str, parts: list[dict], spans: list[tuple[int, int]]) -> list[int]:
    """Part indices the feedback is about, from the line numbers it cites
    (assembled scene lines) and the segment numbers it names. Feedback that
    names neither, or only lines outside every part, goes to every part."""
    hits: set[int] = set()
    for m in _LINE_RE.finditer(feedback or ""):
        line = int(m.group(1))
        for k, (lo, hi) in enumerate(spans):
            if lo <= line <= hi:
                hits.add(k)
    for m in _SEGMENT_RE.finditer(feedback or ""):
        seg = int(m.group(1))
        for k, p in enumerate(parts):
            a, b = p["segments"]
            if a <= seg <= b:
                hits.add(k)
    return sorted(hits) if hits else list(range(len(parts)))


_PART_HEADER = re.compile(r"^    # ── Part (\d+): segments (\d+)-(\d+) ──$")


def split_assembled(code: str, n_segments: int, theme: str) -> list[dict] | None:
    """The parts of a scene `assemble` produced (e.g. a scene.py read back for a
    visual-QA revision), or None when the code is not exactly such a scene."""
    if not code.startswith(SCAFFOLD_IMPORTS):
        return None
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    cls = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ChalkboardScene"), None)
    if cls is None:
        return None
    lines = code.splitlines()
    extra = [ast.get_source_segment(code, n) for n in tree.body
             if isinstance(n, (ast.Import, ast.ImportFrom))
             and n.lineno > SCAFFOLD_IMPORTS.count("\n") + 1]
    parts: list[dict] = []
    for fn in cls.body:
        if not (isinstance(fn, ast.FunctionDef) and fn.name == method_name(len(parts))):
            continue
        m = _PART_HEADER.match(lines[fn.lineno - 2]) if fn.lineno >= 2 else None
        if m is None or int(m.group(1)) != len(parts):
            return None
        src = textwrap.dedent("\n".join(lines[fn.lineno - 1:fn.end_lineno]))
        parts.append({"segments": [int(m.group(2)), int(m.group(3))], "code": src,
                      "imports": extra if not parts else []})
    if not parts:
        return None
    try:
        rebuilt, _ = assemble(parts, n_segments, theme)
    except Exception:
        return None
    return parts if rebuilt == code else None
