# pipeline/agents/script_agent.py
from pipeline.cues import clean_segments, strip_cues
from pipeline.llm import call_json, get_client, has_pdf, web_search_tool
from pipeline.retry import api_call_with_retry, TIMEOUT_SCRIPT_AGENT
from pipeline.state import PipelineState

SYSTEM_PROMPT = """You are an educational script writer. Given a topic, write a clear,
accurate narration script for an animated explainer video. Structure it as distinct
teaching segments (3–8 segments). Each segment should be 1–3 sentences.

Respond with valid JSON only:
{
  "title": "<concise, engaging video title — 4–8 words, Title Case, no trailing punctuation>",
  "script": "<full narration as a single string>",
  "segments": [{"text": "<segment text with [[n]] cue markers>", "estimated_duration_sec": <float>}],
  "needs_web_search": <bool>
}

Title guidelines: write it like a YouTube video title — specific, descriptive, and punchy.
Good: "AWD vs 4WD vs RWD Explained" | Bad: "what is the difference between awd 4wd and rwd?"
Estimate duration as word_count / 2.5 seconds (~150 wpm).

The narration is read aloud by a text-to-speech voice while the animation shows the
notation. Write every piece of math and code the way a lecturer would SAY it:
"e to the x", "x squared", "the derivative of f with respect to x", "the integral from
zero to one", "n log n". Never put symbols, LaTeX, superscripts, slashes, equals signs or
code syntax in the script or segment text (no "e^x", "x^2", "O(n)", "dy/dx", "a = b").
Spell out abbreviations the first time and avoid anything a voice would mispronounce.
Set needs_web_search to true only if the topic requires information beyond your training data.

CUE MARKERS (segment text only): the animation is synced to the narration word by
word. In each segment's "text", put a numbered marker [[1]], [[2]], ... immediately
before the word or phrase at which a new visual should appear or something on screen
should change: an equation or term is introduced, a graph or diagram is drawn, a
label, arrow or highlight lands, a step of a derivation is taken. Use 2 to 5 markers
per segment, numbered from 1 again in every segment, in reading order, at least
about 1.5 seconds of speech apart (roughly four words or more). Put the marker
before the word itself, not before filler: "Start with the [[1]] limit definition,
then [[2]] expand the numerator." Markers are silent: they are removed before the
text is spoken or shown, so the sentence must read naturally without them. Never put
markers in "script"."""

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


SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "script": {"type": "string"},
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Spoken segment text with inline cue markers [[1]], [[2]], ... "
                                       "placed right before the words where visuals land.",
                    },
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
        client = get_client(pdf=has_pdf(context_blocks))

    tools = []
    # Skip web_search if research_agent already provided a brief (effort=high path)
    if (state.get("user_approved_search") or state["effort_level"] == "high") and not state.get("research_brief"):
        tools = [web_search_tool("script")]

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
        return call_json(
            "script", system=SYSTEM_PROMPT, content=content, schema=SCHEMA,
            tools=tools or None, client=client,
        )

    data, _ = await api_call_with_retry(_call, timeout=TIMEOUT_SCRIPT_AGENT, label="script_agent")
    # Segments keep the marked text in `cue_text` (for manim_agent); `text` and
    # `script` are clean prose for everything that reads or speaks them.
    return {
        "title": data.get("title", ""),
        "script": strip_cues(data["script"]),
        "script_segments": clean_segments(data["segments"]),
        "needs_web_search": data.get("needs_web_search", False),
        "status": "validating",
    }
