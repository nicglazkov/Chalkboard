# pipeline/agents/manim_agent.py
import json
import anthropic
from config import CLAUDE_MODEL
from pipeline.retry import api_call_with_retry, TIMEOUT_MANIM_AGENT
from pipeline.state import PipelineState

SYSTEM_PROMPT = """You are an expert Manim Community Edition (v0.20.1) developer.
Generate a complete, runnable Manim scene for an educational animation.

STRICT REQUIREMENTS:
- The scene class MUST be named exactly `ChalkboardScene` (inherits from Scene)
- Use `from manim import *` plus stdlib imports as needed (json, pathlib, etc.)
- Each narration segment gets an animation block followed by self.wait(duration_sec)
- Use self.play(..., run_time=X) for animations
- The code must be syntactically valid Python
- At the start of construct(), load actual segment durations:
    _seg_data = json.loads((Path(__file__).parent / "segments.json").read_text())
    _d = [s["actual_duration_sec"] for s in _seg_data]
    _d = _d + [2.0] * max(0, N - len(_d))
  Replace N with the exact integer from "Total segments: N" shown above.
- Never hardcode a float literal as the argument to self.wait() — always use _d[i]
- When an animation fills part of a segment's time, subtract the animation's run_time from _d[i]:
    self.wait(max(0.0, _d[i] - X))  where X is the numeric value passed to run_time= above

KNOWN API PITFALLS (v0.20.1):
- Brace.get_text(*text) does NOT accept font_size — set it on the returned object: t = brace.get_text('x'); t.scale(0.8)
- VGroup.arrange() returns None — assign before arranging, don't chain
- Always pass run_time as a keyword arg: self.play(anim, run_time=1.0)
- Never use VGroup(*self.mobjects) — self.mobjects can contain non-VMobjects; use *[FadeOut(m) for m in self.mobjects] instead
- Never hardcode self.wait(X) with a float literal — always use _d[i] loaded from segments.json
- Use max(0.0, _d[i] - X) where X is the run_time= value passed to self.play() in that segment
- Pad _d with: _d = _d + [2.0] * max(0, N - len(_d)) where N is the literal integer segment count
- Segment numbers and _d indices are both 0-based — Segment 0 → _d[0], Segment 1 → _d[1]

Respond with JSON only: {"manim_code": "<complete Python code as string>"}"""

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
