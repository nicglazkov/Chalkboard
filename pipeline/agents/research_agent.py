# pipeline/agents/research_agent.py
import json
import anthropic
from config import CLAUDE_MODEL
from pipeline.retry import api_call_with_retry, TIMEOUT_RESEARCH_AGENT
from pipeline.state import PipelineState

SYSTEM_PROMPT = """You are a research assistant preparing material for an educational video script writer.
Given a topic, perform targeted web searches to gather accurate, up-to-date facts, figures, and key points.
Compile them into a concise research brief.

Focus on:
- Core factual claims with specific numbers, dates, or names where relevant
- Common misconceptions to address or avoid
- Current state of knowledge (recent developments)
- 2–5 credible sources

Respond with valid JSON only:
{
  "research_brief": "<compiled research as a readable summary, 150-300 words>",
  "sources": ["<url or citation>"]
}"""


async def research_agent(state: PipelineState, client=None) -> dict:
    if client is None:
        client = anthropic.Anthropic()

    def _call():
        return client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Topic: {state['topic']}"}],
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
                        },
                        "required": ["research_brief", "sources"],
                        "additionalProperties": False,
                    },
                }
            },
        )

    response = await api_call_with_retry(
        _call, timeout=TIMEOUT_RESEARCH_AGENT, label="research_agent"
    )
    data = json.loads(response.content[0].text)
    return {
        "research_brief": data["research_brief"],
        "research_sources": data["sources"],
    }
