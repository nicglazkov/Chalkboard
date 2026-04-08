# pipeline/agents/script_agent.py
import json
import anthropic
from config import CLAUDE_MODEL
from pipeline.retry import api_call_with_retry, TIMEOUT_SCRIPT_AGENT
from pipeline.state import PipelineState

SYSTEM_PROMPT = """You are an educational script writer. Given a topic, write a clear,
accurate narration script for an animated explainer video. Structure it as distinct
teaching segments (3–8 segments). Each segment should be 1–3 sentences.

Respond with valid JSON only:
{
  "title": "<concise, engaging video title — 4–8 words, Title Case, no trailing punctuation>",
  "script": "<full narration as a single string>",
  "segments": [{"text": "<segment text>", "estimated_duration_sec": <float>}],
  "needs_web_search": <bool>
}

Title guidelines: write it like a YouTube video title — specific, descriptive, and punchy.
Good: "AWD vs 4WD vs RWD Explained" | Bad: "what is the difference between awd 4wd and rwd?"
Estimate duration as word_count / 2.5 seconds (~150 wpm).
Set needs_web_search to true only if the topic requires information beyond your training data."""

AUDIENCE_INSTRUCTIONS = {
    "beginner": "Target audience: beginners with no prior knowledge. Use simple vocabulary, avoid jargon, and build from first principles.",
    "intermediate": "Target audience: intermediate learners with some background knowledge. Assume familiarity with basic concepts and explain more advanced ideas clearly.",
    "expert": "Target audience: experts in the field. Use precise technical language, assume deep background knowledge, and focus on nuance and depth.",
}

TONE_INSTRUCTIONS = {
    "casual": "Tone: conversational and friendly, as if explaining to a curious friend.",
    "formal": "Tone: precise and academic, suitable for a university-level lecture.",
    "socratic": "Tone: question-driven — pose key questions before answering them, guiding the viewer to discover insights.",
}


def _build_user_message(state: PipelineState) -> str:
    topic = state["topic"]
    effort = state["effort_level"]
    feedback = state.get("fact_feedback")
    web_approved = state.get("user_approved_search", False)

    msg = f"Topic: {topic}\nEffort level: {effort}"
    msg += f"\n{AUDIENCE_INSTRUCTIONS[state.get('audience', 'intermediate')]}"
    msg += f"\n{TONE_INSTRUCTIONS[state.get('tone', 'casual')]}"

    if state.get("research_brief"):
        msg += f"\n\nResearch brief (ground your script in these facts):\n{state['research_brief']}"
        if state.get("research_sources"):
            sources = "\n".join(f"  - {s}" for s in state["research_sources"])
            msg += f"\n\nSources consulted:\n{sources}"

    if feedback:
        msg += f"\n\nPrevious attempt had issues. Please rewrite the script fully, addressing this feedback:\n{feedback}"
    if web_approved:
        msg += "\n\nWeb search has been approved — use it if needed."
    if effort == "low":
        msg += "\n\nEffort=low: keep the script concise, 3–4 segments, no web search needed."
    return msg


async def script_agent(state: PipelineState, client=None, context_blocks=None) -> dict:
    if client is None:
        has_pdf = context_blocks and any(b.get("type") == "document" for b in context_blocks)
        kwargs = {"default_headers": {"anthropic-beta": "pdfs-2024-09-25"}} if has_pdf else {}
        client = anthropic.Anthropic(**kwargs)

    tools = []
    # Skip web_search if research_agent already provided a brief (effort=high path)
    if (state.get("user_approved_search") or state["effort_level"] == "high") and not state.get("research_brief"):
        tools = [{"type": "web_search_20250305", "name": "web_search"}]

    if context_blocks:
        content = [
            {
                "type": "text",
                "text": "The following files are provided as source material. Use them to inform the script content, facts, and framing:",
            }
        ]
        content.extend(context_blocks)
        content.append({"type": "text", "text": _build_user_message(state)})
    else:
        content = _build_user_message(state)

    def _call():
        return client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
            tools=tools if tools else anthropic.NOT_GIVEN,
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
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
                        "required": ["title", "script", "segments", "needs_web_search"],
                        "additionalProperties": False,
                    },
                }
            },
        )

    response = await api_call_with_retry(_call, timeout=TIMEOUT_SCRIPT_AGENT, label="script_agent")

    text_block = next((b for b in reversed(response.content) if b.type == "text"), None)
    if text_block is None:
        raise RuntimeError(
            f"script_agent: no text block in response. "
            f"Content types: {[b.type for b in response.content]}"
        )
    data = json.loads(text_block.text)
    return {
        "title": data.get("title", ""),
        "script": data["script"],
        "script_segments": data["segments"],
        "needs_web_search": data.get("needs_web_search", False),
        "status": "validating",
    }
