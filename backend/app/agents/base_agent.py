"""Shared machinery for the agents.

An agent here is a narrow, orchestrated component: it is handed a defined
input, renders one prompt, calls the model once, and returns a validated
object. Agents do not call each other and do not decide what runs next — the
workflow does that. This is what keeps the pipeline auditable rather than
emergent.

Every prompt is a file on disk, and its version is derived from its contents.
If someone edits a prompt, findings produced before the edit keep the old
version string, so a stored result never claims to have come from a prompt that
no longer exists.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from app.config import BACKEND_ROOT
from app.llm.client import BaseLLMClient, ImageContent, StructuredResult

logger = logging.getLogger(__name__)

TModel = TypeVar("TModel", bound=BaseModel)

PROMPT_DIR = BACKEND_ROOT / "app" / "prompts"

# Deterministic work — reading a document, mapping a schema — runs at zero
# temperature. Judgement calls get a little room, but not much: a discrepancy
# assessment that changes between runs is not auditable.
TEMPERATURE_EXTRACTION = 0.0
TEMPERATURE_CLASSIFICATION = 0.0
TEMPERATURE_REASONING = 0.1
TEMPERATURE_MAPPING = 0.0

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


@lru_cache(maxsize=32)
def load_prompt(name: str) -> str:
    path = PROMPT_DIR / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"prompt not found: {path}")
    return path.read_text(encoding="utf-8").strip()


@lru_cache(maxsize=32)
def prompt_version(name: str) -> str:
    """A stable identifier for the exact prompt text in use."""
    digest = hashlib.sha256(load_prompt(name).encode("utf-8")).hexdigest()[:8]
    return f"{name}-v1-{digest}"


def render(template: str, values: dict[str, Any]) -> str:
    """Fill {{placeholders}}. An unfilled placeholder is a programming error.

    Left unchecked it would send the literal text "{{evidence}}" to the model,
    which answers confidently about nothing at all.
    """
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", str(value))

    leftover = _PLACEHOLDER.findall(rendered)
    if leftover:
        raise ValueError(f"prompt placeholders were not filled: {sorted(set(leftover))}")
    return rendered


@dataclass
class AgentRun(Generic[TModel]):
    """An agent result together with everything needed to audit it."""

    data: TModel
    model: str
    prompt_version: str
    attempts: int = 1
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: int | None = None

    def provenance(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "prompt_version": self.prompt_version,
            "attempts": self.attempts,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "latency_ms": self.latency_ms,
        }


class BaseAgent:
    """Common wiring: a client, a prompt, and a validated result."""

    #: Prompt file stem in app/prompts.
    prompt_name: str = ""
    #: Default sampling temperature for this agent's task.
    temperature: float = 0.0

    def __init__(self, client: BaseLLMClient | None = None) -> None:
        self._client = client

    @property
    def client(self) -> BaseLLMClient:
        if self._client is None:
            from app.llm import get_llm_client

            self._client = get_llm_client()
        return self._client

    @property
    def system_prompt(self) -> str:
        return load_prompt(self.prompt_name)

    @property
    def version(self) -> str:
        return prompt_version(self.prompt_name)

    def _run(
        self,
        schema: type[TModel],
        *,
        prompt: str,
        system: str | None = None,
        text: str | None = None,
        images: list[ImageContent] | None = None,
        temperature: float | None = None,
    ) -> AgentRun[TModel]:
        result: StructuredResult[TModel] = self.client.analyze_document(
            schema,
            prompt,
            system=system if system is not None else self.system_prompt,
            text=text,
            images=images,
            temperature=self.temperature if temperature is None else temperature,
        )
        return AgentRun(
            data=result.data,
            model=result.model,
            prompt_version=self.version,
            attempts=result.attempts,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            latency_ms=result.latency_ms,
        )


def clip(text: str | None, limit: int) -> str:
    """Bound what is sent to the model.

    Sending an entire 100-page document to answer a question about page 3 is
    the main way these systems get expensive, so every call site clips.
    """
    if not text:
        return ""
    if len(text) <= limit:
        return text
    head = int(limit * 0.7)
    tail = limit - head
    return f"{text[:head]}\n...[{len(text) - limit} characters omitted]...\n{text[-tail:]}"


def path_for_prompt(name: str) -> Path:
    return PROMPT_DIR / f"{name}.txt"
