"""Model client selection.

One place decides which client the application talks to. Everything else asks
for ``get_llm_client()`` and receives something satisfying BaseLLMClient, which
is what makes the model swappable by configuration.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from app.config import settings
from app.llm.client import (
    BaseLLMClient,
    ImageContent,
    LLMError,
    LLMResponse,
    LLMTimeoutError,
    LLMTransportError,
    Message,
    StructuredOutputError,
    StructuredResult,
)

logger = logging.getLogger(__name__)


@lru_cache
def get_llm_client() -> BaseLLMClient:
    if settings.LLM_USE_MOCK:
        if settings.is_production:
            raise RuntimeError("LLM_USE_MOCK must be false in production")
        from app.llm.mock import MockLLMClient

        return MockLLMClient()

    from app.llm.qwen import QwenLLMClient

    logger.info("model client: %s at %s", settings.LLM_MODEL, settings.LLM_BASE_URL)
    return QwenLLMClient()


def reset_llm_client() -> None:
    """Drop the cached client. Used by tests that swap the configuration."""
    get_llm_client.cache_clear()


__all__ = [
    "BaseLLMClient",
    "ImageContent",
    "LLMError",
    "LLMResponse",
    "LLMTimeoutError",
    "LLMTransportError",
    "Message",
    "StructuredOutputError",
    "StructuredResult",
    "get_llm_client",
    "reset_llm_client",
]
