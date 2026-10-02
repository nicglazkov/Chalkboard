# server/models.py
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field


class CreateJobRequest(BaseModel):
    topic: str
    effort: Literal["low", "medium", "high"] = "medium"
    audience: Literal["beginner", "intermediate", "expert"] = "intermediate"
    tone: Literal["casual", "formal", "socratic"] = "casual"
    theme: Literal["chalkboard", "light", "colorful"] = "chalkboard"
    template: Literal["algorithm", "code", "compare", "derivation", "howto", "timeline"] | None = None
    speed: float = Field(1.0, ge=0.25, le=4.0)
    burn_captions: bool = False
    quiz: bool = False
    urls: list[str] = []
    github: list[str] = []
    qa_density: Literal["zero", "normal", "high"] = "normal"
    quality: Literal["low", "medium", "high", "4k"] | None = None   # None = server default
    narrator: Literal["kokoro", "aria", "milo", "alloy"] | None = None  # None = server default


class JobResponse(BaseModel):
    id: str
    status: Literal["pending", "running", "completed", "failed"]
    topic: str
    events: list[dict]
    error: str | None
    output_files: list[str]
    # The job's settings, so a page reopened elsewhere can show what was asked for.
    effort: str | None = None
    quality: str | None = None
    narrator: str | None = None
    qa_density: str | None = None
    quiz: bool = False
    burn_captions: bool = False
    template: str | None = None
