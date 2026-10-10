# pipeline/state.py
from typing import TypedDict, Literal
from pydantic import BaseModel


class ValidationResult(BaseModel):
    verdict: Literal["approved", "needs_revision"]
    feedback: str


class PipelineState(TypedDict):
    topic: str
    title: str
    run_id: str
    script: str
    script_segments: list[dict]
    manim_code: str
    script_attempts: int
    code_attempts: int
    fact_feedback: str | None
    code_feedback: str | None
    effort_level: Literal["low", "medium", "high"]
    audience: Literal["beginner", "intermediate", "expert"]
    tone: Literal["casual", "formal", "socratic"]
    theme: Literal["chalkboard", "light", "colorful"]
    needs_web_search: bool
    user_approved_search: bool
    status: Literal["drafting", "validating", "needs_user_input", "approved", "failed"]
    context_file_paths: list[str]
    speed: float
    pace: str | None      # "relaxed" | "normal" | "brisk"; None = PACE env, else relaxed (pipeline/pacing.py)
    template: str | None
    research_brief: str | None
    research_sources: list[str]
    search_warning: str | None
    interactive: bool
    narrator: str | None  # pipeline/tts/voices.py name; None = NARRATOR / TTS_BACKEND default
    quality: str | None   # "low" | "medium" | "high" | "4k"; None = MANIM_QUALITY
    layout_renderable: bool  # last dry-run completed (only layout violations, no crash)
    claude_review_failures: int     # consecutive advisory rejections by Claude's code review
    claude_reviews: int             # Claude code reviews run so far (capped by CODE_REVIEW_ROUNDS)
    code_feedback_advisory: bool    # current code_feedback came from Claude review, not a hard check
    scene_parts: list[dict] | None  # scene written in parts: [{"segments": [a, b], "code", "imports"}] (manim_agent)
    scene_plan: dict | None         # the shared visual plan those parts follow
