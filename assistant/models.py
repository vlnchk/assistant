"""Typed contracts shared by Telegram, the LLM router and local evaluations."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class ModelUsage:
    prompt_tokens: int | None = None
    candidate_tokens: int | None = None
    thoughts_tokens: int | None = None
    total_tokens: int | None = None

    def as_dict(self) -> dict[str, int | None]:
        return asdict(self)


@dataclass(slots=True)
class RouteDecision:
    model: str
    tool_name: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0
    usage: ModelUsage = field(default_factory=ModelUsage)
    error: str | None = None


@dataclass(slots=True)
class ToolOutcome:
    reply_text: str | None = None
    handover_reason: str | None = None
    admin_notification: str | None = None
    dry_run: bool = False


@dataclass(slots=True)
class TurnRequest:
    message: str
    history: list[dict[str, Any]] = field(default_factory=list)
    user_context: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TurnTrace:
    model_name: str
    model_path: list[str] = field(default_factory=list)
    routes: list[RouteDecision] = field(default_factory=list)
    tool_ms: float = 0.0
    total_ms: float = 0.0

    @property
    def model_ms(self) -> float:
        return sum(route.latency_ms for route in self.routes)

    @property
    def total_tokens(self) -> int | None:
        values = [
            route.usage.total_tokens
            for route in self.routes
            if route.usage.total_tokens is not None
        ]
        return sum(values) if values else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "model_path": " -> ".join(self.model_path),
            "model_ms": round(self.model_ms, 2),
            "tool_ms": round(self.tool_ms, 2),
            "total_ms": round(self.total_ms, 2),
            "total_tokens": self.total_tokens,
        }


@dataclass(slots=True)
class TurnResult:
    reply_text: str | None
    handover_reason: str | None = None
    admin_notification: str | None = None
    tool_name: str | None = None
    tool_arguments: dict[str, Any] = field(default_factory=dict)
    trace: TurnTrace | None = None
    error: str | None = None
    dry_run: bool = False
