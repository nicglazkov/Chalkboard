# pipeline/agents/code_validator.py
import ast
import json
import anthropic
from config import CLAUDE_MODEL
from pipeline.retry import api_call_with_retry, TIMEOUT_CODE_VALIDATOR
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


async def code_validator(state: PipelineState, client=None) -> dict:
    code = state["manim_code"]
    attempts = state["code_attempts"]

    # Step 1: syntax check (free, fast — no Claude call)
    try:
        ast.parse(code)
    except SyntaxError as e:
        return {
            "code_feedback": f"Syntax error: {e}",
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
        f"Is the class named ChalkboardScene?\n\n"
        f"Sync check: The scene must load _seg_data from (Path(__file__).parent / \"segments.json\") "
        f"and use _d[i] (not hardcoded float literals) for all self.wait() calls. "
        f"If any self.wait() call uses a hardcoded float literal, return needs_revision.\n\n"
        f"CONFIRMED CORRECT v0.20.1 APIs (do NOT flag these as errors):\n"
        f"- Code(code_string=\"...\", language=\"python\", background=\"window\", paragraph_config={{\"font_size\": N}}) — correct constructor\n"
        f"- code_obj.code_lines[i] — correct way to access the i-th line (VGroup); .code attribute does not exist\n"
        f"- VGroup(*self.mobjects) is invalid if non-VMobjects present; *[FadeOut(m) for m in self.mobjects] is correct\n"
        f"- self.wait(0) is invalid; guard with: _r = max(0.0, x); if _r > 0: self.wait(_r)\n\n"
        f"Cleanup check: For each segment block after the first (marked by '# ── Segment N:' "
        f"comments where N > 0), verify the code clears the previous segment's tracked mobjects "
        f"via self.play(*[FadeOut(m) for m in seg_items], ...) BEFORE introducing any new content. "
        f"If any segment N > 0 introduces new animations without first fading out the prior "
        f"segment's elements, return needs_revision."
    )

    def _call():
        return client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": user_msg}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        )

    response = await api_call_with_retry(_call, timeout=TIMEOUT_CODE_VALIDATOR, label="code_validator")

    result = ValidationResult.model_validate_json(response.content[0].text)
    if result.verdict == "needs_revision":
        return {
            "code_feedback": result.feedback,
            "code_attempts": attempts + 1,
        }
    else:
        # Clear code_feedback on approval so _after_code_validator routes to render_trigger
        return {
            "code_feedback": None,
            "code_attempts": attempts,
        }
