# pipeline/agents/code_validator.py
import ast
import json
import anthropic as _anthropic_module
from config import CLAUDE_MODEL


class _AProxy:
    """Per-module proxy so unittest.mock.patch can target this module's Anthropic independently."""
    def __getattr__(self, name):
        return getattr(_anthropic_module, name)
    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)


anthropic = _AProxy()
from pipeline.state import PipelineState, ValidationResult

SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["approved", "needs_revision"]},
        "feedback": {"type": "string"},
    },
    "required": ["verdict", "feedback"],
    "additionalProperties": False,
}


def code_validator(state: PipelineState, client=None) -> dict:
    code = state["manim_code"]
    attempts = state["code_attempts"]

    # Step 1: syntax check (free, fast — no Claude call)
    try:
        ast.parse(code)
    except SyntaxError as e:
        return {
            "code_feedback": f"Syntax error: {e}",
            "code_verdict": "needs_revision",
            "code_attempts": attempts + 1,
        }

    # Step 2: semantic review via Claude
    if client is None:
        client = anthropic.Anthropic()
    user_msg = (
        f"Review this Manim CE code for correctness and coherence with the script.\n\n"
        f"Script:\n{state['script']}\n\n"
        f"Manim code:\n{code}\n\n"
        f"Check: Does the animation visualize the script? Are Manim CE v0.20 APIs used correctly? "
        f"Is the class named ChalkboardScene?"
    )

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": user_msg}],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
    )

    result = ValidationResult.model_validate_json(response.content[0].text)
    updates: dict = {"code_feedback": result.feedback, "code_verdict": result.verdict}
    if result.verdict == "needs_revision":
        updates["code_attempts"] = attempts + 1
    else:
        updates["code_attempts"] = attempts  # explicitly return on pass too
    return updates
