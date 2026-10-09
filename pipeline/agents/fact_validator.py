# pipeline/agents/fact_validator.py
from pipeline.cues import strip_cues
from pipeline.llm import call_json_budgeted
from pipeline.retry import TimeoutExhausted, TIMEOUT_FACT_VALIDATOR
from pipeline.state import PipelineState, ValidationResult
from pipeline import telemetry

# Script rewrites allowed before the run escalates (graph._after_fact_validator).
SCRIPT_ATTEMPT_LIMIT = 3

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
    effort = state["effort_level"]
    instruction = EFFORT_INSTRUCTIONS[effort]

    user_msg = (
        f"Review the factual accuracy of this educational script.\n"
        f"Instructions: {instruction}\n\n"
        f"Script:\n{strip_cues(state['script'])}"
    )

    try:
        data, _ = await call_json_budgeted(
            "fact", label="fact_validator", timeout=TIMEOUT_FACT_VALIDATOR, max_tokens=16000,
            content=user_msg, schema=SCHEMA, client=client,
        )
    except TimeoutExhausted as e:
        print(f"  [fact_validator] review unavailable, keeping the script unreviewed ({e})")
        return {"fact_feedback": None, "script_attempts": state["script_attempts"]}

    result = ValidationResult.model_validate(data)

    if result.verdict == "needs_revision":
        attempts = state["script_attempts"] + 1
        if attempts >= SCRIPT_ATTEMPT_LIMIT and not state.get("interactive", True):
            # Non-interactive runs (--yes, the server) have nobody to escalate
            # to; escalation would end the run with no video. Keep the latest
            # script, which already went through every earlier round of fixes,
            # and record the reviewer's remaining notes on the run.
            print(f"  [fact_validator] still has notes after {attempts} script attempts; "
                  f"non-interactive run, continuing with the latest script (notes kept in run_stats.json)")
            telemetry.emit("warning", {"stage": "fact_check", "message":
                                       f"fact check unresolved after {attempts} script attempts",
                                       "feedback": result.feedback})
            return {"fact_feedback": None, "script_attempts": attempts}
        return {
            "fact_feedback": result.feedback,
            "script_attempts": attempts,
        }
    else:
        # Clear fact_feedback on approval so _after_fact_validator routes to manim_agent
        return {
            "fact_feedback": None,
            "script_attempts": state["script_attempts"],
        }
