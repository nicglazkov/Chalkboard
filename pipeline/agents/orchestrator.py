# pipeline/agents/orchestrator.py
import asyncio
from pipeline.state import PipelineState


def _build_escalation_message(state: PipelineState) -> str:
    lines = ["=" * 60, "PIPELINE ESCALATION — Maximum retries reached", "=" * 60]

    if state["script_attempts"] >= 3:
        lines.append(f"\nScript validation failed after 3 attempts.")
        lines.append(f"Last feedback: {state.get('fact_feedback', 'None')}")
    if state["code_attempts"] >= 3:
        lines.append(f"\nCode validation failed after 3 attempts.")
        lines.append(f"Last feedback: {state.get('code_feedback', 'None')}")

    lines.append("\nOptions:")
    lines.append('  {"action": "retry_script", "guidance": "<your guidance>"}')
    lines.append('  {"action": "retry_code",   "guidance": "<your guidance>"}')
    lines.append('  {"action": "abort",         "guidance": ""}')
    return "\n".join(lines)


async def escalate_to_user(state: PipelineState) -> dict:
    message = _build_escalation_message(state)
    print(message)

    if not state.get("interactive", True):
        print("  [non-interactive mode — auto-aborting]")
        return {"status": "failed"}

    print("\nEnter action (retry_script / retry_code / abort):")
    try:
        action = (await asyncio.to_thread(input, "  action: ")).strip()
        guidance = ""
        if action in ("retry_script", "retry_code"):
            guidance = (await asyncio.to_thread(input, "  guidance: ")).strip()
    except EOFError:
        print("  [non-interactive stdin — defaulting to abort]")
        action = "abort"
        guidance = ""

    if action == "retry_script":
        return {
            "script_attempts": 0,
            "fact_feedback": guidance,
            "code_feedback": None,  # clear stale code feedback
            "status": "drafting",
        }
    elif action == "retry_code":
        return {
            "code_attempts": 0,
            "code_feedback": guidance,
            "fact_feedback": None,  # clear stale script feedback
            "status": "validating",
        }
    else:
        return {"status": "failed"}
