"""Stable optional LLM service contract for plugins.

An absent service (context.service("llm") is None) is normal. Generated data
is never evidence for acquisition rights, bibliographic identity or age.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping, Protocol, runtime_checkable

from .models import TaskControl


@dataclass(frozen=True, slots=True)
class LLMRequest:
    """Small text request; 'structured' asks for a JSON object."""
    prompt: str
    task: Literal["generate", "structured"] = "generate"
    system: str = ""
    max_output_tokens: int = 256


@dataclass(frozen=True, slots=True)
class LLMResult:
    ok: bool
    text: str = ""
    structured: Mapping[str, object] = field(default_factory=dict)
    reason: str = ""
    provider: str = ""
    model: str = ""
    generated: bool = False
    remote: bool = False


@runtime_checkable
class LLMService(Protocol):
    """Optional Core-owned service resolved through context.service('llm')."""
    def available(self) -> bool: ...
    def capabilities(self) -> tuple[str, ...]: ...
    def generate(self, request: LLMRequest, *, control: TaskControl | None = None) -> LLMResult: ...


__all__ = ["LLMRequest", "LLMResult", "LLMService"]
