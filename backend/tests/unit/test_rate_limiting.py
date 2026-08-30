"""Rate-limit handling: throttling and Retry-After.

Free-tier endpoints cap requests per minute and per day. Spacing requests
costs seconds; discovering the cap through 429s costs failed calls and a slice
of a daily quota. Both mechanisms are tested with an injected clock, so the
suite does not actually wait.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.llm.client import LLMTimeoutError, Message
from app.llm.qwen import QwenLLMClient, _retry_after_seconds
from app.llm.retry import RetryPolicy, with_retries

BASE_URL = "http://endpoint/v1"
ENDPOINT = f"{BASE_URL}/chat/completions"


def completion(text: str = "ok") -> dict:
    return {
        "model": "m",
        "choices": [{"message": {"role": "assistant", "content": text},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }


class FakeClock:
    """A clock that only advances when something sleeps."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


def build(clock: FakeClock, *, interval: float) -> QwenLLMClient:
    return QwenLLMClient(
        base_url=BASE_URL,
        model="m",
        api_key="k",
        timeout=5,
        max_retries=3,
        min_request_interval=interval,
        client=httpx.Client(timeout=5),
        clock=clock.time,
        sleeper=clock.sleep,
    )


class TestThrottle:
    @respx.mock
    def test_the_first_request_is_not_delayed(self, clock):
        respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=completion()))
        client = build(clock, interval=3.0)

        client.generate([Message(role="user", content="x")])

        assert clock.slept == []

    @respx.mock
    def test_back_to_back_requests_are_spaced(self, clock):
        respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=completion()))
        client = build(clock, interval=3.0)

        client.generate([Message(role="user", content="x")])
        client.generate([Message(role="user", content="y")])
        client.generate([Message(role="user", content="z")])

        # Two gaps for three requests, each the full interval.
        assert clock.slept == [3.0, 3.0]

    @respx.mock
    def test_time_already_spent_counts_towards_the_gap(self, clock):
        respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=completion()))
        client = build(clock, interval=3.0)

        client.generate([Message(role="user", content="x")])
        # A slow document parse happens between calls; that time is not slept
        # again, or throughput would halve for no reason.
        clock.advance(2.5)
        client.generate([Message(role="user", content="y")])

        assert clock.slept == pytest.approx([0.5])

    @respx.mock
    def test_a_long_gap_needs_no_wait(self, clock):
        respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=completion()))
        client = build(clock, interval=3.0)

        client.generate([Message(role="user", content="x")])
        clock.advance(60)
        client.generate([Message(role="user", content="y")])

        assert clock.slept == []

    @respx.mock
    def test_throttling_is_off_by_default(self, clock):
        respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=completion()))
        client = build(clock, interval=0.0)

        client.generate([Message(role="user", content="x")])
        client.generate([Message(role="user", content="y")])

        assert clock.slept == []

    @respx.mock
    def test_retries_are_throttled_too(self, clock):
        # A retry is another request against the same quota.
        respx.post(ENDPOINT).mock(
            side_effect=[
                httpx.Response(500, text="boom"),
                httpx.Response(200, json=completion()),
            ]
        )
        client = build(clock, interval=3.0)

        client.generate([Message(role="user", content="x")])

        assert 3.0 in clock.slept


class TestRetryAfter:
    def test_seconds_are_parsed(self):
        response = httpx.Response(429, headers={"retry-after": "12"})
        assert _retry_after_seconds(response) == 12.0

    def test_a_missing_header_is_none(self):
        assert _retry_after_seconds(httpx.Response(429)) is None

    def test_a_date_format_is_ignored_rather_than_crashing(self):
        response = httpx.Response(429, headers={"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"})
        assert _retry_after_seconds(response) is None

    def test_an_absurd_wait_is_capped(self):
        # A gateway asking for an hour should surface as a failure, not stall
        # a worker silently.
        response = httpx.Response(429, headers={"retry-after": "3600"})
        assert _retry_after_seconds(response) == 60.0

    def test_the_policy_prefers_the_server_hint_over_its_own_backoff(self):
        slept: list[float] = []

        class RateLimited(Exception):
            retry_after = 9.0

        attempts = {"n": 0}

        def operation(attempt: int) -> str:
            attempts["n"] = attempt
            if attempt == 1:
                raise RateLimited()
            return "done"

        result = with_retries(
            operation,
            policy=RetryPolicy(max_attempts=3, base_delay=0.5),
            retry_on=(RateLimited,),
            sleeper=slept.append,
        )

        assert result == "done"
        assert slept == [9.0]

    def test_its_own_backoff_wins_when_that_is_longer(self):
        slept: list[float] = []

        class Slow(Exception):
            retry_after = 0.1

        def operation(attempt: int) -> str:
            if attempt < 2:
                raise Slow()
            return "done"

        with_retries(
            operation,
            policy=RetryPolicy(max_attempts=3, base_delay=2.0, jitter=0.0),
            retry_on=(Slow,),
            sleeper=slept.append,
        )

        assert slept == [2.0]

    @respx.mock
    def test_a_rate_limited_call_waits_as_instructed_then_succeeds(self, clock):
        respx.post(ENDPOINT).mock(
            side_effect=[
                httpx.Response(429, headers={"retry-after": "5"}, text="slow down"),
                httpx.Response(200, json=completion("recovered")),
            ]
        )
        client = build(clock, interval=0.0)

        response = client.generate([Message(role="user", content="x")])

        assert response.text == "recovered"

    @respx.mock
    def test_persistent_rate_limiting_still_fails_cleanly(self, clock):
        respx.post(ENDPOINT).mock(
            return_value=httpx.Response(429, headers={"retry-after": "1"}, text="no")
        )
        client = build(clock, interval=0.0)

        with pytest.raises(LLMTimeoutError):
            client.generate([Message(role="user", content="x")])
