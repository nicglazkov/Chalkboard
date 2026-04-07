# pipeline/agents/research_agent.py
import json
import anthropic
from config import CLAUDE_MODEL
from pipeline.retry import api_call_with_retry, TIMEOUT_RESEARCH_AGENT, TimeoutExhausted
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


async def research_agent(state: PipelineState, client=None) -> dict:
    if client is None:
        client = anthropic.Anthropic()

    def _call():
        return client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Topic: {state['topic']}"}],
            # Always enabled: research_agent is only invoked on effort_level="high" (graph routing guarantees this)
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "research_brief": {"type": "string"},
                            "sources": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "search_warning": {
                                "type": ["string", "null"],
                            },
                        },
                        "required": ["research_brief", "sources", "search_warning"],
                        "additionalProperties": False,
                    },
                }
            },
        )

    try:
        response = await api_call_with_retry(
            _call, timeout=TIMEOUT_RESEARCH_AGENT, label="research_agent"
        )
    except TimeoutExhausted as e:
        warning = f"Web search failed after all retries ({e}) — script will rely on training data only."
        return {
            "research_brief": None,
            "research_sources": [],
            "search_warning": warning,
        }

    text_block = next((b for b in reversed(response.content) if b.type == "text"), None)
    if text_block is None:
        warning = (
            f"Web search ran but returned no readable response "
            f"(content types: {[b.type for b in response.content]}) — "
            f"script will rely on training data only."
        )
        return {
            "research_brief": None,
            "research_sources": [],
            "search_warning": warning,
        }

    data = json.loads(text_block.text)
    return {
        "research_brief": data["research_brief"],
        "research_sources": data["sources"],
        "search_warning": data.get("search_warning"),
    }
