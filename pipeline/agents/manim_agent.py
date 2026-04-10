# pipeline/agents/manim_agent.py
import json
import anthropic
from config import CLAUDE_MODEL
from pipeline.retry import api_call_with_retry, TIMEOUT_MANIM_AGENT
from pipeline.state import PipelineState

SYSTEM_PROMPT = """You are an expert Manim Community Edition (v0.20.1) developer.
Generate a complete, runnable Manim scene for an educational animation.

STRICT REQUIREMENTS:
- The scene class MUST be named exactly `ChalkboardScene` and inherit from BOTH ChalkboardSceneBase and Scene:
    from chalkboard_base import ChalkboardSceneBase
    class ChalkboardScene(ChalkboardSceneBase, Scene):
- Use `from manim import *` plus stdlib imports as needed (json, pathlib, etc.)
- Each narration segment gets an animation block followed by self.wait(duration_sec)
- At the start of EVERY segment block, call self.begin_segment(N, duration=_d[N]) where N is the 0-based segment index
- At the END of construct(), call self.end_layout_check() BEFORE the final FadeOut cleanup:
    self.end_layout_check()
    self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.5)
- Use self.play(..., run_time=X) for animations
- The code must be syntactically valid Python
- At the start of construct(), load actual segment durations:
    _seg_data = json.loads((Path(__file__).parent / "segments.json").read_text())
    _d = [s["actual_duration_sec"] for s in _seg_data]
    _d = _d + [2.0] * max(0, N - len(_d))
  Replace N with the exact integer from "Total segments: N" shown above.
- Never hardcode a float literal as the argument to self.wait() — always use _d[i]
- When an animation fills part of a segment's time, subtract the animation's run_time from _d[i].
  IMPORTANT: self.wait(0) raises ValueError — Manim requires duration > 0. Always guard:
    _r = max(0.0, _d[i] - X)
    if _r > 0:
        self.wait(_r)
  NEVER write self.wait(max(0.0, ...)) directly — if the value is 0.0 it will crash.

KNOWN API PITFALLS (v0.20.1):
- Brace.get_text(*text) does NOT accept font_size — set it on the returned object: t = brace.get_text('x'); t.scale(0.8)
- VGroup.arrange() returns None — assign before arranging, don't chain
- Always pass run_time as a keyword arg: self.play(anim, run_time=1.0)
- Never use VGroup(*self.mobjects) — self.mobjects can contain non-VMobjects; use *[FadeOut(m) for m in self.mobjects] instead
- DASHED is not a Manim constant — use DashedLine(start, end, ...) instead of Line(start, end, line_style=DASHED) or any other DASHED usage; DashedLine accepts the same positional and keyword args as Line
- Never hardcode self.wait(X) with a float literal — always use _d[i] loaded from segments.json
- self.wait(0) crashes — always guard: _r = max(0.0, _d[i] - X); if _r > 0: self.wait(_r)
- Pad _d with: _d = _d + [2.0] * max(0, N - len(_d)) where N is the literal integer segment count
- Segment numbers and _d indices are both 0-based — Segment 0 → _d[0], Segment 1 → _d[1]
- When pointer labels (i, j, L, R triangles + text) AND a descriptive text line both appear below an array, use at least buff=0.8 between the array and the descriptive text so pointers don't overlap it — e.g. desc.next_to(arr, DOWN, buff=0.85)
- Never animate a label's position relative to a pointer using j_ptr.copy().next_to(...) inside .animate — .animate captures positions before the frame; instead pass the destination coordinate directly: j_label.animate.move_to(lom_boxes[j_pos].get_top() + UP * 0.55)
- Code object (v0.20.1): use `code_string=` NOT `code=`, and `paragraph_config={"font_size": N}` NOT `font_size=N` — both wrong kwargs raise TypeError. Use `Code(code_string="...", language="python", background="window", paragraph_config={"font_size": 22})`. Access lines via `code_obj.code_lines[i]` NOT `code_obj.code[i]` — the `.code` attribute does not exist in v0.20.1.

LAYOUT RULES — required for every scene:

Canvas: x ∈ [−7.11, +7.11], y ∈ [−4.0, +4.0]. Use these named anchor points:
  title_anchor  = UP * 3.5               # persistent title — full width
  left_anchor   = LEFT * 3.5 + UP * 0.5  # code, arrays, diagrams (left half)
  right_anchor  = RIGHT * 3.5 + UP * 0.5 # callouts, annotations (right half)
  center_anchor = ORIGIN                  # full-width single element
  bottom_anchor = DOWN * 3.5             # step counter, status, one-line warnings

Placement rules:
1. Anchor primary elements with move_to(zone_anchor) or to_edge(). Avoid chaining
   next_to() through more than 2 elements from a fixed point — coordinate drift
   accumulates and causes elements to land in unexpected positions.
2. When LEFT and RIGHT zones are both populated, keep left content within x < −0.5
   and right content within x > +0.5. Never let the two zones overlap.
   BOUNDING BOX CHECK — required before placing any horizontal group:
   For N elements of width W placed side by side with leftmost center at x_0:
     right_edge = x_0 + (N − 1) × W + W/2  (or equivalently x_0 + (N − 0.5) × W)
   This right_edge must be < −0.5 for LEFT zone content.
   Example: 3 columns, W=1.9, leftmost center x_0=−3.5 → right_edge = −3.5 + 2.5×1.9 = 1.25 — WRONG.
   Fix: reduce W to ≤ 1.3 (right_edge = −3.5 + 2.5×1.3 = −0.25, still marginal) or
   shift center left to x_0 = −4.5 (right_edge = −4.5 + 2.5×1.9 = 0.25 — still too wide),
   or use at most 2 columns in the left zone.
   Rule of thumb: with center x_0 = −3.5, max total width for left zone = 3.0 units
   (e.g. 3 cols × W=0.9, or 2 cols × W=1.4).

CLEAN SLATE rule — mandatory at every segment boundary:
3. Track all mobjects added in a segment by appending them to a list as you create them:
     seg_items = []
     elem = Text(...); self.play(FadeIn(elem)); seg_items.append(elem)
4. At the start of every segment after the first, BEFORE introducing any new content:
     self.play(*[FadeOut(m) for m in seg_items], run_time=0.5)
     seg_items = []
5. The persistent title is NEVER added to seg_items.
6. A multi-segment element (e.g. a code block spanning segments 1–3) is excluded from
   seg_items; FadeOut it explicitly at the segment where it is no longer needed.
7. Leaving mobjects from a prior segment on screen while starting a new segment is the
   primary cause of visual overlap — treat this rule as strictly as the self.wait(0) guard.
8. Call self.begin_segment(N, duration=_d[N]) at the start of each segment block (right after the
   '# ── Segment N:' comment). Call self.end_layout_check() at the end of construct() BEFORE the
   final FadeOut. These are required — code_validator will reject code missing them.

REQUIRED SCAFFOLD — every scene must follow this structure exactly:

from chalkboard_base import ChalkboardSceneBase
from manim import *
import json
from pathlib import Path

class ChalkboardScene(ChalkboardSceneBase, Scene):
    def construct(self):
        _seg_data = json.loads((Path(__file__).parent / "segments.json").read_text())
        _d = [s["actual_duration_sec"] for s in _seg_data]
        _d = _d + [2.0] * max(0, N - len(_d))  # N = total segment count (integer)

        # ── Segment 0: <title> ──
        self.begin_segment(0, duration=_d[0])
        seg_items = []
        # ... animations ...
        _r = max(0.0, _d[0] - <animation_time>)
        if _r > 0:
            self.wait(_r)

        # ── Segment 1: <title> ──
        self.play(*[FadeOut(m) for m in seg_items], run_time=0.5)
        seg_items = []
        self.begin_segment(1, duration=_d[1])
        # ... animations ...

        # ── End ──
        self.end_layout_check()
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.5)

Respond with JSON only: {"manim_code": "<complete Python code as string>"}"""

TEMPLATE_SPECS = {
    "algorithm": (
        "ANIMATION TEMPLATE — algorithm step-through:\n"
        "Structure the scene as a series of named algorithm steps, one per narration segment.\n"
        "Required visual elements:\n"
        "- Array/list: represent elements as a row of RoundedRectangle cells (fill_color=\"#2E2E2E\",\n"
        "  stroke_color=primary), each with a centered Text value label.\n"
        "- Index pointers: use downward-pointing Triangle objects below the relevant cell, with a\n"
        "  Text label (i, j, lo, hi, pivot, etc.) below the triangle. Keep buff ≥ 0.85 between\n"
        "  pointer labels and any descriptive text below the array.\n"
        "- Active/pivot element: fill with accent color; restore to primary when no longer active.\n"
        "- Step counter: Text(\"Step N\") anchored to the top-right corner, updated each step with\n"
        "  FadeOut/FadeIn or Transform.\n"
        "- Swaps: animate both elements moving simultaneously — do not teleport.\n"
        "Each narration segment corresponds to one discrete algorithm state shown visually."
    ),
    "code": (
        "ANIMATION TEMPLATE — code walkthrough:\n"
        "Structure the scene to reveal and annotate source code incrementally.\n"
        "Required visual elements:\n"
        "- Use Manim's Code object for syntax-highlighted source (v0.20.1 API):\n"
        "    Code(code_string=\"...\", language=\"python\", background=\"window\",\n"
        "         paragraph_config={\"font_size\": 22})\n"
        "  CRITICAL: use code_string= NOT code=, and paragraph_config={\"font_size\":N} NOT font_size=N.\n"
        "  Both wrong kwargs raise TypeError.\n"
        "  Place it left-center or center of the frame.\n"
        "- Access individual lines via code_obj.code_lines[i] (zero-indexed VGroup per line).\n"
        "  CRITICAL: the attribute is code_lines, NOT code. code_obj.code does not exist.\n"
        "- Reveal lines progressively: FadeIn(code_obj.code_lines[i]) for individual lines.\n"
        "- Highlight the active line: code_obj.code_lines[i].animate.set_color(accent).\n"
        "  Restore inactive lines to primary color when moving on.\n"
        "- Callout: a short Text label (font_size ≤ 26) placed to the right of the highlighted\n"
        "  line, FadeIn when the line is discussed, FadeOut before moving on.\n"
        "- Animate complete line objects only — never animate individual characters within a line.\n"
        "- Do NOT use Code.highlight_lines() — not a valid v0.20.1 API. Use set_color() instead."
    ),
    "compare": (
        "ANIMATION TEMPLATE — side-by-side comparison (A vs B):\n"
        "Structure the scene as two clearly labeled columns with a visual divider.\n"
        "Required visual elements:\n"
        "- Vertical divider: DashedLine from (ORIGIN + UP*3) to (ORIGIN + DOWN*3) at x=0.\n"
        "- Left column (x center ≈ -3.5): option A — color all its elements with secondary.\n"
        "- Right column (x center ≈ +3.5): option B — color all its elements with accent.\n"
        "- Column headers: bold Text objects near the top of each column (y ≈ 2.8).\n"
        "- Introduce paired traits together: animate left item then right item in the same step\n"
        "  (LaggedStart or simultaneous FadeIn), so the viewer sees both sides of each point.\n"
        "- End with a summary row or small table at the bottom highlighting the key trade-off.\n"
        "- Hard constraint: left content must stay within x < -0.5; right content within x > +0.5.\n"
        "  Never let elements from one column overlap the other or the divider.\n"
        "  For tables/grids in the left column: verify right_edge = x_0 + (N − 0.5) × W < −0.5\n"
        "  before committing to column count and width (see BOUNDING BOX CHECK in LAYOUT RULES)."
    ),
    "howto": (
        "ANIMATION TEMPLATE — how-to steps:\n"
        "Structure the scene as a numbered step list that builds up progressively, one step per narration segment.\n"
        "Required visual elements:\n"
        "- Step items: each step is a row containing a circled number (Circle + Text) on the left and\n"
        "  a short Text description to its right, grouped in a VGroup.\n"
        "- Progressive reveal: FadeIn each new step row at the bottom of the growing list.\n"
        "- Active step highlight: set the current step's circle fill to accent color and its text to\n"
        "  primary color at full opacity.\n"
        "- Completed steps: once narration moves past a step, dim it — set circle fill to a muted\n"
        "  shade (e.g. interpolate_color(accent, background, 0.6)) and reduce text opacity to 0.4.\n"
        "- Vertical layout: arrange steps from the top down using left_anchor as the starting point.\n"
        "  Space rows with buff=0.55 so they don't crowd. Keep all step rows within x < +3.5 to\n"
        "  leave room for an optional illustration or icon on the right side.\n"
        "- Optional right-side visual: a small diagram, icon, or annotation in the right_anchor zone\n"
        "  that updates per step (FadeOut old, FadeIn new). Omit if content is purely textual.\n"
        "- Final recap: in the last segment, briefly re-highlight all steps simultaneously (set all\n"
        "  circles back to accent) before the closing FadeOut."
    ),
    "timeline": (
        "ANIMATION TEMPLATE — chronological timeline:\n"
        "Structure the scene around a horizontal timeline axis with dated event markers.\n"
        "Required visual elements:\n"
        "- Timeline axis: a horizontal Line spanning roughly x ∈ [−6.0, +6.0] at y = 0 (center_anchor),\n"
        "  with small Arrow tips or Dot endpoints.\n"
        "- Event markers: for each event, place a Dot on the axis at the appropriate x position.\n"
        "  Above or below the dot, add a short Text date/year label (font_size ≤ 22) and a brief\n"
        "  Text description (font_size ≤ 24). Alternate above/below to avoid crowding.\n"
        "- Chronological animation: reveal events left to right, one per narration segment.\n"
        "  Use GrowFromCenter for the Dot and FadeIn for the label group.\n"
        "- Active event: highlight the current event's dot and label with accent color.\n"
        "  Revert previous events to secondary or a dimmed primary.\n"
        "- Connector lines: use short vertical Line segments (height ~0.3) from each Dot to its\n"
        "  label group so the association is visually clear.\n"
        "- Scale to fit: if there are more than 6 events, scale the entire timeline VGroup to fit\n"
        "  within the canvas or split across two rows (top row for earlier events, bottom for later).\n"
        "- Title: place the scene title at title_anchor as usual; the timeline axis sits below it."
    ),
}

THEME_SPECS = {
    "chalkboard": (
        "COLOR THEME — chalkboard (dark background):\n"
        "  Set self.camera.background_color = \"#1C1C1C\" at the start of construct().\n"
        "  primary = ManimColor(\"#F5F0E8\")   # cream — use for main text and shapes\n"
        "  accent  = ManimColor(\"#E8D44D\")   # chalk yellow — use for highlights\n"
        "  secondary = ManimColor(\"#6EC6E8\") # sky blue — use for secondary elements"
    ),
    "light": (
        "COLOR THEME — light (bright background):\n"
        "  Set self.camera.background_color = \"#FAFAFA\" at the start of construct().\n"
        "  primary = ManimColor(\"#1A1A1A\")   # near-black — use for main text and shapes\n"
        "  accent  = ManimColor(\"#2563EB\")   # blue — use for highlights\n"
        "  secondary = ManimColor(\"#16A34A\") # green — use for secondary elements"
    ),
    "colorful": (
        "COLOR THEME — colorful (vibrant):\n"
        "  Set self.camera.background_color = BLACK at the start of construct().\n"
        "  Use Manim's built-in color constants (RED, BLUE, GREEN, YELLOW, ORANGE, PURPLE, TEAL).\n"
        "  Vary colors across elements to create a visually dynamic animation."
    ),
}


def _format_segments(segments: list[dict]) -> str:
    n = len(segments)
    header = f"Total segments: {n} (use _d[0] through _d[{max(0, n-1)}])"
    lines = [header]
    for i, seg in enumerate(segments):  # 0-based
        duration = seg.get("estimated_duration_sec", 0.0)
        text = seg.get("text", "")
        lines.append(f"  Segment {i} — est. {duration:.1f}s — use _d[{i}] at runtime: {text}")
    return "\n".join(lines)


async def manim_agent(state: PipelineState, client=None, context_blocks=None) -> dict:
    if client is None:
        has_pdf = context_blocks and any(b.get("type") == "document" for b in context_blocks)
        kwargs = {"default_headers": {"anthropic-beta": "pdfs-2024-09-25"}} if has_pdf else {}
        client = anthropic.Anthropic(**kwargs)

    user_msg = (
        f"Create a Manim animation for this educational script.\n\n"
        f"Topic: {state['topic']}\n\n"
        f"Narration segments with timings:\n{_format_segments(state['script_segments'])}\n\n"
        f"Full script for context:\n{state['script']}\n\n"
        f"{THEME_SPECS[state.get('theme', 'chalkboard')]}"
    )

    template = state.get("template")
    if template and template in TEMPLATE_SPECS:
        user_msg += f"\n\n{TEMPLATE_SPECS[template]}"

    if state.get("code_feedback"):
        user_msg += f"\n\nPrevious attempt had issues. Rewrite the scene fully, addressing:\n{state['code_feedback']}"

    if context_blocks:
        content = [
            {
                "type": "text",
                "text": "The following files are provided as source material. Use them to inform what the animation should visualize:",
            }
        ]
        content.extend(context_blocks)
        content.append({"type": "text", "text": user_msg})
    else:
        content = user_msg

    def _call():
        return client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=16384,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": {
                        "type": "object",
                        "properties": {"manim_code": {"type": "string"}},
                        "required": ["manim_code"],
                        "additionalProperties": False,
                    },
                }
            },
        )

    response = await api_call_with_retry(_call, timeout=TIMEOUT_MANIM_AGENT, label="manim_agent")

    data = json.loads(response.content[0].text)
    return {"manim_code": data["manim_code"], "status": "validating"}
