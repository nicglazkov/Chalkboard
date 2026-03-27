# pipeline/graph.py
import uuid
from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph, START, END
from config import CHECKPOINT_DB
from pipeline.state import PipelineState
from pipeline.agents.script_agent import script_agent
from pipeline.agents.fact_validator import fact_validator
from pipeline.agents.manim_agent import manim_agent
from pipeline.agents.code_validator import code_validator
from pipeline.agents.orchestrator import escalate_to_user
from pipeline.render_trigger import render_trigger


def _after_fact_validator(state: PipelineState) -> str:
    if not state.get("fact_feedback"):  # approved
        return "manim_agent"
    if state["script_attempts"] >= 3:
        return "escalate_to_user"
    return "script_agent"


def _after_code_validator(state: PipelineState) -> str:
    if not state.get("code_feedback"):  # approved
        return "render_trigger"
    if state["code_attempts"] >= 3:
        return "escalate_to_user"
    return "manim_agent"


def _after_escalate(state: PipelineState) -> str:
    if state["status"] == "failed":
        return END
    if state["script_attempts"] == 0 and state.get("fact_feedback"):
        # attempts were reset by escalate_to_user — retry script
        return "script_agent"
    if state["code_attempts"] == 0 and state.get("code_feedback"):
        # attempts were reset by escalate_to_user — retry code
        return "manim_agent"
    return END


def _init_state(state: PipelineState, config: RunnableConfig | None = None) -> dict:
    """Populate default fields at graph entry."""
    run_id = (config or {}).get("configurable", {}).get("thread_id", str(uuid.uuid4()))
    return {
        "run_id": run_id,
        "script": state.get("script", ""),
        "script_segments": state.get("script_segments", []),
        "manim_code": state.get("manim_code", ""),
        "script_attempts": state.get("script_attempts", 0),
        "code_attempts": state.get("code_attempts", 0),
        "fact_feedback": state.get("fact_feedback"),
        "code_feedback": state.get("code_feedback"),
        "needs_web_search": state.get("needs_web_search", False),
        "user_approved_search": state.get("user_approved_search", False),
        "audience": state.get("audience", "intermediate"),
        "tone": state.get("tone", "casual"),
        "status": "drafting",
    }


def build_graph(checkpointer=None) -> StateGraph:
    builder = StateGraph(PipelineState)

    builder.add_node("init", _init_state)
    builder.add_node("script_agent", script_agent)
    builder.add_node("fact_validator", fact_validator)
    builder.add_node("manim_agent", manim_agent)
    builder.add_node("code_validator", code_validator)
    builder.add_node("escalate_to_user", escalate_to_user)
    builder.add_node("render_trigger", render_trigger)

    builder.add_edge(START, "init")
    builder.add_edge("init", "script_agent")
    builder.add_edge("script_agent", "fact_validator")
    builder.add_conditional_edges("fact_validator", _after_fact_validator,
        ["script_agent", "manim_agent", "escalate_to_user"])
    builder.add_edge("manim_agent", "code_validator")
    builder.add_conditional_edges("code_validator", _after_code_validator,
        ["manim_agent", "render_trigger", "escalate_to_user"])
    builder.add_edge("render_trigger", END)
    builder.add_conditional_edges("escalate_to_user", _after_escalate,
        ["script_agent", "manim_agent", END])

    return builder.compile(checkpointer=checkpointer)


# Note: callers manage the AsyncSqliteSaver context manager themselves (see main.py).
# Do not add a helper that returns the graph from inside a context manager — the
# connection closes when the context exits, making the returned graph unusable.
