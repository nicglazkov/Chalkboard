# pipeline/agents/manim_agent.py
import json
import anthropic as _anthropic_module
from config import CLAUDE_MODEL
from pipeline.state import PipelineState


class _AProxy:
    """Per-module proxy so unittest.mock.patch can target this module's Anthropic independently."""
    def __getattr__(self, name):
        return getattr(_anthropic_module, name)
    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)


anthropic = _AProxy()

SYSTEM_PROMPT = """You are an expert Manim Community Edition (v0.20.1) developer.
Generate a complete, runnable Manim scene for an educational animation.

STRICT REQUIREMENTS:
- The scene class MUST be named exactly `ChalkboardScene` (inherits from Scene)
- Use `from manim import *` as the only import
- Each narration segment gets an animation block followed by self.wait(duration_sec)
- Use self.play(..., run_time=X) for animations
- The code must be syntactically valid Python

Respond with JSON only: {"manim_code": "<complete Python code as string>"}"""


def _format_segments(segments: list[dict]) -> str:
    lines = []
    for i, seg in enumerate(segments, 1):
        duration = seg.get("estimated_duration_sec", 0.0)
        text = seg.get("text", "")
        lines.append(f"  Segment {i} ({duration:.1f}s): {text}")
    return "\n".join(lines)


def manim_agent(state: PipelineState, client=None) -> dict:
    if client is None:
        client = anthropic.Anthropic()

    user_msg = (
        f"Create a Manim animation for this educational script.\n\n"
        f"Topic: {state['topic']}\n\n"
        f"Narration segments with timings:\n{_format_segments(state['script_segments'])}\n\n"
        f"Full script for context:\n{state['script']}"
    )

    if state.get("code_feedback"):
        user_msg += f"\n\nPrevious attempt had issues. Rewrite the scene fully, addressing:\n{state['code_feedback']}"

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
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

    data = json.loads(response.content[0].text)
    return {"manim_code": data["manim_code"], "status": "validating"}
