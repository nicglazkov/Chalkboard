# pipeline/agents/script_agent.py
import json
import anthropic
from config import CLAUDE_MODEL
from pipeline.state import PipelineState

SYSTEM_PROMPT = """You are an educational script writer. Given a topic, write a clear,
accurate narration script for an animated explainer video. Structure it as distinct
teaching segments (3–8 segments). Each segment should be 1–3 sentences.

Respond with valid JSON only:
{
  "script": "<full narration as a single string>",
  "segments": [{"text": "<segment text>", "estimated_duration_sec": <float>}],
  "needs_web_search": <bool>
}

Estimate duration as word_count / 2.5 seconds (~150 wpm).
Set needs_web_search to true only if the topic requires information beyond your training data."""

AUDIENCE_INSTRUCTIONS = {
    "beginner": "Target audience: beginners with no prior knowledge. Use simple vocabulary, avoid jargon, and build from first principles.",
    "intermediate": "Target audience: intermediate learners with some background knowledge. Assume familiarity with basic concepts and explain more advanced ideas clearly.",
    "expert": "Target audience: experts in the field. Use precise technical language, assume deep background knowledge, and focus on nuance and depth.",
}


def _build_user_message(state: PipelineState) -> str:
    topic = state["topic"]
    effort = state["effort_level"]
    feedback = state.get("fact_feedback")
    web_approved = state.get("user_approved_search", False)

    msg = f"Topic: {topic}\nEffort level: {effort}"
    msg += f"\n{AUDIENCE_INSTRUCTIONS[state.get('audience', 'intermediate')]}"
    if feedback:
        msg += f"\n\nPrevious attempt had issues. Please rewrite the script fully, addressing this feedback:\n{feedback}"
    if web_approved:
        msg += "\n\nWeb search has been approved — use it if needed."
    if effort == "low":
        msg += "\n\nEffort=low: keep the script concise, 3–4 segments, no web search needed."
    return msg


def script_agent(state: PipelineState, client=None) -> dict:
    if client is None:
        client = anthropic.Anthropic()

    tools = []
    # Per spec: effort=high always enables web search (no approval gate needed)
    if state.get("user_approved_search") or state["effort_level"] == "high":
        tools = [{"type": "web_search_20250305", "name": "web_search"}]

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_user_message(state)}],
        tools=tools if tools else anthropic.NOT_GIVEN,
        output_config={
            "format": {
                "type": "json_schema",
                "schema": {
                    "type": "object",
                    "properties": {
                        "script": {"type": "string"},
                        "segments": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "text": {"type": "string"},
                                    "estimated_duration_sec": {"type": "number"},
                                },
                                "required": ["text", "estimated_duration_sec"],
                                "additionalProperties": False,
                            },
                        },
                        "needs_web_search": {"type": "boolean"},
                    },
                    "required": ["script", "segments", "needs_web_search"],
                    "additionalProperties": False,
                },
            }
        },
    )

    data = json.loads(response.content[0].text)
    return {
        "script": data["script"],
        "script_segments": data["segments"],
        "needs_web_search": data.get("needs_web_search", False),
        "status": "validating",
    }
