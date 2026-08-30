"""OpenAI-compatible client, defaulting to Qwen3-VL-8B-Instruct.

The wire format is the /chat/completions API, so this one class serves vLLM,
SGLang, a llama.cpp server, or OpenAI itself. Nothing about Qwen is hardcoded
beyond the default model name in configuration; the class is named for the
model it is tuned against, not for a model it requires.

Images are inlined as base64 data URIs in the standard image_url content part,
which is what Qwen3-VL expects through an OpenAI-compatible server.
"""

from __future__ import annotations

import base64
import logging
import threading
import time
from collections.abc import Callable
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.config import settings
from app.llm.client import (
    BaseLLMClient,
    ImageContent,
    LLMResponse,
    LLMTimeoutError,
    LLMTransportError,
    Message,
    StructuredOutputError,
    StructuredResult,
)
from app.llm.retry import RETRYABLE_STATUS, RetryPolicy, with_retries
from app.llm.structured_output import (
    correction_message,
    describe_validation_error,
    extract_json,
    schema_summary,
    validate_payload,
)

logger = logging.getLogger(__name__)

TModel = TypeVar("TModel", bound=BaseModel)


class _Retryable(RuntimeError):
    """Transport-level failure worth another attempt.

    ``retry_after`` carries the server's own instruction when it sent one, so
    a rate limiter is obeyed rather than guessed at.
    """

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class QwenLLMClient(BaseLLMClient):
    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        max_retries: int | None = None,
        min_request_interval: float | None = None,
        client: httpx.Client | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = (base_url or settings.LLM_BASE_URL).rstrip("/")
        self.model = model or settings.LLM_MODEL
        self.api_key = api_key or settings.LLM_API_KEY
        self.timeout = timeout or settings.LLM_TIMEOUT
        self.max_tokens = max_tokens or settings.LLM_MAX_TOKENS
        self.temperature = settings.LLM_TEMPERATURE if temperature is None else temperature
        self.max_retries = max_retries or settings.LLM_MAX_RETRIES
        self.min_request_interval = (
            settings.LLM_MIN_REQUEST_INTERVAL
            if min_request_interval is None
            else min_request_interval
        )
        self._client = client or _build_http_client(self.timeout)
        # Injected so the throttle is testable without real waiting.
        self._clock = clock
        self._sleep = sleeper
        self._lock = threading.Lock()
        self._last_request_at: float | None = None
        # Some servers reject response_format. The first rejection turns it off
        # for the process rather than failing every subsequent call.
        self._supports_json_mode = True

    # ------------------------------------------------------------------ wire
    def _headers(self) -> dict[str, str]:
        return {
            "content-type": "application/json",
            "authorization": f"Bearer {self.api_key}",
            **(settings.LLM_EXTRA_HEADERS or {}),
        }

    def _throttle(self) -> None:
        """Hold each request at least ``min_request_interval`` after the last.

        Waiting a second before a request costs a second. Being rate limited
        costs a failed request, a backoff, and a slice of a daily quota that
        free tiers hand out sparingly.
        """
        if self.min_request_interval <= 0:
            return
        with self._lock:
            now = self._clock()
            if self._last_request_at is not None:
                elapsed = now - self._last_request_at
                remaining = self.min_request_interval - elapsed
                if remaining > 0:
                    self._sleep(remaining)
                    now = self._clock()
            self._last_request_at = now

    @staticmethod
    def _content_parts(message: Message) -> str | list[dict[str, Any]]:
        if not message.images:
            return message.content
        parts: list[dict[str, Any]] = [{"type": "text", "text": message.content}]
        for image in message.images:
            encoded = base64.b64encode(image.data).decode("ascii")
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{image.media_type};base64,{encoded}"},
                }
            )
        return parts

    def _payload(
        self,
        messages: list[Message],
        *,
        temperature: float | None,
        max_tokens: int | None,
        json_mode: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": m.role, "content": self._content_parts(m)} for m in messages
            ],
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": max_tokens or self.max_tokens,
        }
        if json_mode and self._supports_json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._throttle()
        try:
            response = self._client.post(
                f"{self.base_url}/chat/completions", json=payload, headers=self._headers()
            )
        except httpx.TimeoutException as exc:
            raise _Retryable(f"timeout after {self.timeout}s") from exc
        except httpx.HTTPError as exc:
            # The message matters here: "ConnectError" alone cannot be told
            # apart from DNS failure, refused connection or a dropped socket.
            raise _Retryable(f"transport error: {type(exc).__name__}: {exc}") from exc

        if response.status_code in RETRYABLE_STATUS:
            raise _Retryable(
                f"server returned {response.status_code}",
                retry_after=_retry_after_seconds(response),
            )
        if response.status_code == 400 and "response_format" in response.text:
            # Stricter servers reject JSON mode outright; drop it and retry.
            self._supports_json_mode = False
            raise _Retryable("server rejected response_format; retrying without JSON mode")
        if response.status_code >= 400:
            raise LLMTransportError(
                f"model server returned {response.status_code}: {response.text[:200]}"
            )
        return response.json()

    def _call(
        self,
        messages: list[Message],
        *,
        temperature: float | None,
        max_tokens: int | None,
        json_mode: bool = False,
    ) -> LLMResponse:
        started = time.perf_counter()
        policy = RetryPolicy(max_attempts=self.max_retries)
        attempts_used = {"n": 0}

        def attempt(attempt_number: int) -> dict[str, Any]:
            attempts_used["n"] = attempt_number
            payload = self._payload(
                messages, temperature=temperature, max_tokens=max_tokens, json_mode=json_mode
            )
            return self._post(payload)

        try:
            body = with_retries(
                attempt, policy=policy, retry_on=(_Retryable,), description="chat completion"
            )
        except _Retryable as exc:
            raise LLMTimeoutError(
                f"model call failed after {policy.max_attempts} attempts: {exc}"
            ) from exc

        choice = (body.get("choices") or [{}])[0]
        text = (choice.get("message") or {}).get("content") or ""
        usage = body.get("usage") or {}
        return LLMResponse(
            text=text,
            model=body.get("model", self.model),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            latency_ms=int((time.perf_counter() - started) * 1000),
            attempts=attempts_used["n"],
            finish_reason=choice.get("finish_reason"),
        )

    # -------------------------------------------------------------- interface
    def generate(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        return self._call(messages, temperature=temperature, max_tokens=max_tokens)

    def generate_structured(
        self,
        messages: list[Message],
        schema: type[TModel],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        max_retries: int | None = None,
    ) -> StructuredResult[TModel]:
        budget = max_retries or self.max_retries
        conversation = list(messages)
        last_raw = ""
        last_problem = ""

        for attempt in range(1, budget + 1):
            response = self._call(
                conversation,
                temperature=temperature,
                max_tokens=max_tokens,
                json_mode=True,
            )
            last_raw = response.text
            try:
                payload = extract_json(response.text)
                data = validate_payload(payload, schema)
            except ValidationError as exc:
                last_problem = describe_validation_error(exc)
            except ValueError as exc:
                last_problem = str(exc)
            else:
                return StructuredResult(
                    data=data,
                    raw_text=response.text,
                    model=response.model,
                    attempts=attempt,
                    prompt_tokens=response.prompt_tokens,
                    completion_tokens=response.completion_tokens,
                    latency_ms=response.latency_ms,
                )

            logger.warning(
                "structured output rejected on attempt %d/%d for %s",
                attempt,
                budget,
                schema.__name__,
            )
            if attempt < budget:
                # The document stays in context, so the correction turn is kept
                # short rather than repeating it.
                conversation = [
                    *conversation,
                    Message(role="assistant", content=response.text),
                    Message(role="user", content=correction_message(last_problem, schema)),
                ]

        raise StructuredOutputError(
            f"{schema.__name__} could not be produced in {budget} attempts: {last_problem}",
            raw=last_raw,
            attempts=budget,
        )

    def analyze_image(
        self,
        image: bytes | ImageContent,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float | None = None,
    ) -> str:
        content = image if isinstance(image, ImageContent) else ImageContent(data=image)
        messages: list[Message] = []
        if system:
            messages.append(Message(role="system", content=system))
        messages.append(Message(role="user", content=prompt, images=[content]))
        return self._call(messages, temperature=temperature, max_tokens=None).text

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
        messages: list[Message] = []
        if system:
            messages.append(Message(role="system", content=system))

        body = prompt
        if text:
            body = f"{prompt}\n\nDOCUMENT TEXT:\n{text}"
        body = f"{body}\n\nReturn JSON matching this schema:\n{schema_summary(schema)}"
        messages.append(Message(role="user", content=body, images=images or []))

        return self.generate_structured(
            messages, schema, temperature=temperature, max_retries=max_retries
        )

    def close(self) -> None:
        self._client.close()


def _build_http_client(timeout: float) -> httpx.Client:
    """An HTTP client shaped for slow, widely spaced requests.

    Keep-alive is disabled deliberately. Requests here are seconds apart and
    throttled further on free tiers, so pooled connections sit idle long enough
    for the far end to close them; the next write then fails on a dead socket,
    and because the pool hands back the same connection, every retry fails the
    same way. A fresh connection costs a TLS handshake, which is nothing beside
    a multi-second model call.

    ``retries=2`` covers connection establishment specifically, which the
    application-level retry cannot distinguish from a model failure.
    """
    limits = httpx.Limits(max_keepalive_connections=0, max_connections=10)
    return httpx.Client(
        timeout=timeout,
        limits=limits,
        transport=httpx.HTTPTransport(retries=2, limits=limits),
    )


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Parse Retry-After, which rate limiters send as seconds."""
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        # Capped: a gateway asking for ten minutes should surface as a failure
        # rather than silently stalling a worker for that long.
        return min(float(raw), 60.0)
    except ValueError:
        return None
