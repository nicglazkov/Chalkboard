# pipeline/agents/research_agent.py
from pipeline.llm import call_json_budgeted, web_search_tool
from pipeline.retry import TIMEOUT_RESEARCH_AGENT, TimeoutExhausted
from pipeline.state import PipelineState

SYSTEM_PROMPT = """You are a research assistant preparing material for an educational video script writer.
Given a topic, perform targeted web searches to gather accurate, up-to-date facts, figures, and key points.
Compile them into a concise research brief.

Focus on:
- Core factual claims with specific numbers, dates, or names where relevant
- Common misconceptions to address or avoid
- Current state of knowledge (recent developments)
- 2–5 credible sources (in the `sources` array use brief citation strings like "Title — domain.com" or a URL, NOT long paragraphs of text)

After searching, assess whether your results are genuinely relevant to the topic asked.
Set search_warning to a short plain-English sentence if any of these apply:
- Search results are clearly about a different topic than what was asked
- Results contradict the topic's premise in a significant way the script writer should know about
- You found very little or no relevant information and the brief relies mostly on prior knowledge
Otherwise set search_warning to null."""


SCHEMA = {
    "type": "object",
    "properties": {
        "research_brief": {"type": "string"},
        "sources": {"type": "array", "items": {"type": "string"}},
        "search_warning": {"type": ["string", "null"]},
    },
    "required": ["research_brief", "sources", "search_warning"],
    "additionalProperties": False,
}


async def research_agent(state: PipelineState, client=None) -> dict:
    try:
        # Web search always on: research_agent only runs at effort_level="high"
        # (graph routing guarantees this).
        data, _ = await call_json_budgeted(
            "research", label="research_agent", timeout=TIMEOUT_RESEARCH_AGENT, max_tokens=32000,
            system=SYSTEM_PROMPT, content=f"Topic: {state['topic']}",
            schema=SCHEMA, tools=[web_search_tool("research")], client=client,
        )
    except (TimeoutExhausted, RuntimeError, ValueError) as e:
        warning = f"Web search failed after all retries ({e}) — script will rely on training data only."
        return {
            "research_brief": None,
            "research_sources": [],
            "search_warning": warning,
        }

    return {
        "research_brief": data["research_brief"],
        "research_sources": data["sources"],
        "search_warning": data.get("search_warning"),
    }
