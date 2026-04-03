# pipeline/agents/fact_validator.py
import anthropic
from config import CLAUDE_MODEL
from pipeline.retry import api_call_with_retry, TIMEOUT_FACT_VALIDATOR
from pipeline.state import PipelineState, ValidationResult

EFFORT_INSTRUCTIONS = {
    "low": "Do a light check only. Flag only obvious factual errors. Approve if generally correct.",
    "medium": "Spot-check the key claims. Only flag claims that are clearly and definitively incorrect. If a claim is plausible, uncertain, or merely worth double-checking, approve the script — reserve needs_revision for clear factual errors, not uncertainty.",
    "high": "Thorough fact-check. Flag anything uncertain, unverified, or potentially misleading.",
}

SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["approved", "needs_revision"]},
        "feedback": {"type": "string"},
    },
    "required": ["verdict", "feedback"],
    "additionalProperties": False,
}


async def fact_validator(state: PipelineState, client=None) -> dict:
    if client is None:
        client = anthropic.Anthropic()
    effort = state["effort_level"]
    instruction = EFFORT_INSTRUCTIONS[effort]

    user_msg = (
        f"Review the factual accuracy of this educational script.\n"
        f"Instructions: {instruction}\n\n"
        f"Script:\n{state['script']}"
    )

    def _call():
        return client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": user_msg}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        )

    response = await api_call_with_retry(_call, timeout=TIMEOUT_FACT_VALIDATOR, label="fact_validator")

    result = ValidationResult.model_validate_json(response.content[0].text)

    if result.verdict == "needs_revision":
        return {
            "fact_feedback": result.feedback,
            "script_attempts": state["script_attempts"] + 1,
        }
    else:
        # Clear fact_feedback on approval so _after_fact_validator routes to manim_agent
        return {
            "fact_feedback": None,
            "script_attempts": state["script_attempts"],
        }
