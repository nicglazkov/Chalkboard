# pipeline/state.py
from typing import TypedDict, Literal
from pydantic import BaseModel


class ValidationResult(BaseModel):
    verdict: Literal["approved", "needs_revision"]
    feedback: str


class PipelineState(TypedDict):
    topic: str
    run_id: str
    script: str
    script_segments: list[dict]
    manim_code: str
    script_attempts: int
    code_attempts: int
    fact_feedback: str | None
    code_feedback: str | None
    effort_level: Literal["low", "medium", "high"]
    needs_web_search: bool
    user_approved_search: bool
    status: Literal["drafting", "validating", "needs_user_input", "approved", "failed"]
