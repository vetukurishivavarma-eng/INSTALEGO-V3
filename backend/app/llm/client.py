"""The model interface the rest of the application programs against.

Nothing outside this package knows which model is in use. Agents ask for a
completion or a structured object; the client decides how to talk to whatever
OpenAI-compatible endpoint is configured. Swapping Qwen3-VL-8B for the 32B
variant, or for a different server entirely, is a configuration change.

Every response carries the model name, prompt version and token counts, because
a finding has to remain explainable months after it was produced.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel

TModel = TypeVar("TModel", bound=BaseModel)

Role = Literal["system", "user", "assistant"]


class LLMError(RuntimeError):
    """Base class for model failures, carrying an ErrorCode member."""

    code = "LLM_INVALID_OUTPUT"


class LLMTimeoutError(LLMError):
    code = "LLM_TIMEOUT"


class LLMTransportError(LLMError):
    code = "LLM_TIMEOUT"


class StructuredOutputError(LLMError):
    """The model answered, but not with the shape that was asked for."""

    code = "LLM_INVALID_OUTPUT"

    def __init__(self, message: str, *, raw: str | None = None, attempts: int = 0) -> None:
        super().__init__(message)
        self.raw = raw
        self.attempts = attempts


@dataclass
class ImageContent:
    data: bytes
    media_type: str = "image/png"


@dataclass
class Message:
    role: Role
    content: str
    images: list[ImageContent] = field(default_factory=list)


@dataclass
class LLMResponse:
    text: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: int | None = None
    attempts: int = 1
    finish_reason: str | None = None

    @property
    def total_tokens(self) -> int | None:
        if self.prompt_tokens is None or self.completion_tokens is None:
            return None
        return self.prompt_tokens + self.completion_tokens


@dataclass
class StructuredResult(Generic[TModel]):
    """A parsed, schema-valid object plus the metadata needed to audit it."""

    data: TModel
    raw_text: str
    model: str
    attempts: int = 1
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: int | None = None


class BaseLLMClient(abc.ABC):
    """Minimal surface the agents depend on."""

    model: str

    @abc.abstractmethod
    def generate(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse: ...

    @abc.abstractmethod
    def generate_structured(
        self,
        messages: list[Message],
        schema: type[TModel],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        max_retries: int | None = None,
    ) -> StructuredResult[TModel]:
        """Return an instance of ``schema``, retrying on malformed output.

        Implementations must never return a partially valid object: if the
        model cannot produce the schema within the retry budget, they raise
        StructuredOutputError so the caller can record a real failure rather
        than proceed on a guess.
        """

    @abc.abstractmethod
    def analyze_image(
        self,
        image: bytes | ImageContent,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float | None = None,
    ) -> str:
        """Free-text answer about a single image, used for transcription."""

    @abc.abstractmethod
    def analyze_document(
        self,
        schema: type[TModel],
        prompt: str,
        *,
        system: str | None = None,
        text: str | None = None,
        images: list[ImageContent] | None = None,
        temperature: float | None = None,
        max_retries: int | None = None,
    ) -> StructuredResult[TModel]:
        """Structured answer about a document, from text, images or both.

        This is the call the extraction agents make. Passing both text and
        images is normal: a scanned page contributes pixels while its sibling
        pages contribute a text layer.
        """

    def describe(self) -> dict[str, Any]:
        """Provenance recorded alongside anything this client produced."""
        return {"model": self.model, "client": type(self).__name__}
