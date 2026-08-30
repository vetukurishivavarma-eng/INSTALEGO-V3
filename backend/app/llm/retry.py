"""Retry policy for model calls.

Two kinds of failure are worth retrying: the endpoint was briefly unavailable,
and the model returned something unusable. They are retried differently — a
transport failure just waits and repeats, while bad output gets a correction
turn appended so the second attempt has a reason to be better.

Nothing here logs prompts or responses. A retry log line names the attempt, the
error class and the model, and no document content whatsoever.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


@dataclass
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0
    jitter: float = 0.25

    def delay_for(self, attempt: int) -> float:
        """Exponential backoff with jitter, so parallel workers do not
        re-collide on a model server that is already struggling."""
        delay = min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)
        return delay + random.uniform(0, self.jitter * delay)


def with_retries(
    operation: Callable[[int], T],
    *,
    policy: RetryPolicy,
    retry_on: tuple[type[Exception], ...],
    description: str = "llm call",
    sleeper: Callable[[float], None] = time.sleep,
) -> T:
    """Run ``operation(attempt)`` until it succeeds or the budget runs out."""
    last: Exception | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return operation(attempt)
        except retry_on as exc:
            last = exc
            if attempt >= policy.max_attempts:
                break
            # A server that says how long to wait knows better than our
            # backoff curve does.
            hinted = getattr(exc, "retry_after", None)
            wait = max(policy.delay_for(attempt), float(hinted or 0.0))
            logger.warning(
                "%s failed (attempt %d/%d): %s; retrying in %.2fs",
                description,
                attempt,
                policy.max_attempts,
                type(exc).__name__,
                wait,
            )
            sleeper(wait)
    assert last is not None
    raise last
