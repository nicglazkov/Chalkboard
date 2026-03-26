# pipeline/state.py
from typing import TypedDict, Literal
from pydantic import BaseModel


class ValidationResult(BaseModel):
    verdict: Literal["approved", "needs_revision"]
    feedback: str


class PipelineState(TypedDict):
    topic: str
    run_id: str                    # from config["configurable"]["thread_id"]
    script: str
    script_segments: list[dict]    # [{text: str, estimated_duration_sec: float}]
    manim_code: str
    script_attempts: int           # 0–3
    code_attempts: int             # 0–3
    fact_feedback: str | None
    code_feedback: str | None
    effort_level: Literal["low", "medium", "high"]
    needs_web_search: bool
    user_approved_search: bool
    status: Literal["drafting", "validating", "needs_user_input", "approved", "failed"]
