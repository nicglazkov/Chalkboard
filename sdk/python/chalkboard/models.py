"""Typed response models. Pydantic-free — plain dataclasses with a
`from_dict` classmethod so the SDK's only runtime dependency is httpx.

The shape mirrors `server/models.py`. We don't share the module
between the server and the SDK because the server's dataclasses
import config / asyncpg / firebase — too much surface for an end-user
package."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class JobResponse:
    id: str
    status: Literal["pending", "running", "completed", "failed", "cancelled"]
    topic: str
    events: list[dict] = field(default_factory=list)
    error: str | None = None
    output_files: list[str] = field(default_factory=list)
    created_at: str | None = None
    mode: Literal["live", "test"] = "live"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "JobResponse":
        return cls(
            id=d["id"], status=d["status"], topic=d["topic"],
            events=list(d.get("events") or []),
            error=d.get("error"),
            output_files=list(d.get("output_files") or []),
            created_at=d.get("created_at"),
            mode=d.get("mode", "live"),
        )


@dataclass
class VideoMeta:
    run_id: str
    user_id: str = ""
    topic: str = ""
    title: str = ""
    created_at: str = ""
    duration_sec: float = 0.0
    quality: str = "medium"
    thumb_path: str | None = None
    script: str = ""
    effort: str = "medium"
    audience: str = "intermediate"
    tone: str = "casual"
    theme: str = "chalkboard"
    template: str | None = None
    speed: float = 1.0
    status: str = "completed"
    model: str = ""
    narrator: str = ""
    test_mode: bool = False
    output_files: list[str] = field(default_factory=list)
    urls: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "VideoMeta":
        return cls(
            run_id=d["run_id"],
            user_id=d.get("user_id", ""),
            topic=d.get("topic", ""),
            title=d.get("title", ""),
            created_at=d.get("created_at", ""),
            duration_sec=float(d.get("duration_sec", 0.0)),
            quality=d.get("quality", "medium"),
            thumb_path=d.get("thumb_path"),
            script=d.get("script", ""),
            effort=d.get("effort", "medium"),
            audience=d.get("audience", "intermediate"),
            tone=d.get("tone", "casual"),
            theme=d.get("theme", "chalkboard"),
            template=d.get("template"),
            speed=float(d.get("speed", 1.0)),
            status=d.get("status", "completed"),
            model=d.get("model", ""),
            narrator=d.get("narrator", ""),
            test_mode=bool(d.get("test_mode", False)),
            output_files=list(d.get("output_files") or []),
            urls=dict(d.get("urls") or {}),
        )


@dataclass
class ApiKeyMeta:
    id: str
    name: str
    prefix: str
    hint: str
    created_at: str
    last_used_at: str | None
    revoked_at: str | None
    expires_at: str | None
    is_active: bool

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ApiKeyMeta":
        return cls(
            id=d["id"], name=d["name"], prefix=d["prefix"], hint=d["hint"],
            created_at=d["created_at"],
            last_used_at=d.get("last_used_at"),
            revoked_at=d.get("revoked_at"),
            expires_at=d.get("expires_at"),
            is_active=bool(d.get("is_active", False)),
        )


@dataclass
class WebhookMeta:
    id: str
    url: str
    events: list[str]
    description: str
    created_at: str
    disabled_at: str | None
    consecutive_failures: int
    last_delivered_at: str | None
    is_active: bool

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "WebhookMeta":
        return cls(
            id=d["id"], url=d["url"],
            events=list(d.get("events") or []),
            description=d.get("description", ""),
            created_at=d["created_at"],
            disabled_at=d.get("disabled_at"),
            consecutive_failures=int(d.get("consecutive_failures", 0)),
            last_delivered_at=d.get("last_delivered_at"),
            is_active=bool(d.get("is_active", False)),
        )
