# server/models.py
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel


class CreateJobRequest(BaseModel):
    topic: str
    effort: Literal["low", "medium", "high"] = "medium"
    audience: Literal["beginner", "intermediate", "expert"] = "intermediate"
    tone: Literal["casual", "formal", "socratic"] = "casual"
    theme: Literal["chalkboard", "light", "colorful"] = "chalkboard"
    template: str | None = None
    speed: float = 1.0


class JobResponse(BaseModel):
    id: str
    status: Literal["pending", "running", "completed", "failed"]
    topic: str
    events: list[dict]
    error: str | None
    output_files: list[str]
