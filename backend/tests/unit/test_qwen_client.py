"""The OpenAI-compatible client, against a mocked endpoint.

This is the seam the mock client never touches: request shaping, image
encoding, retries, JSON-mode negotiation and the structured-output correction
loop. All of it has to be right before a real model is attached, because a bug
here looks exactly like a bad model.

respx intercepts at the transport, so the real httpx code path runs.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest
import respx
from pydantic import BaseModel, Field

from app.llm.client import (
    ImageContent,
    LLMTimeoutError,
    LLMTransportError,
    Message,
    StructuredOutputError,
)
from app.llm.qwen import QwenLLMClient

BASE_URL = "http://model-server:8000/v1"
ENDPOINT = f"{BASE_URL}/chat/completions"


class Extracted(BaseModel):
    field: str
    value: str
    confidence: float = Field(ge=0.0, le=1.0)


def completion(content: str, *, model: str = "Qwen3-VL-8B-Instruct") -> dict:
    return {
        "id": "chatcmpl-test",
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
    }


@pytest.fixture
def client():
    # No sleeping between retries: the backoff is tested by counting calls,
    # not by waiting for them.
    return QwenLLMClient(
        base_url=BASE_URL,
        model="Qwen3-VL-8B-Instruct",
        api_key="test-key",
        timeout=5,
        max_retries=3,
        client=httpx.Client(timeout=5),
    )


class TestRequestShaping:
    @respx.mock
    def test_authorisation_and_model_are_sent(self, client):
        route = respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=completion("hi")))

        client.generate([Message(role="user", content="hello")])

        request = route.calls.last.request
        assert request.headers["authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        assert payload["model"] == "Qwen3-VL-8B-Instruct"
        assert payload["messages"] == [{"role": "user", "content": "hello"}]

    @respx.mock
    def test_extraction_runs_at_zero_temperature(self, client):
        route = respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=completion("{}")))

        client.generate([Message(role="user", content="x")], temperature=0.0)

        assert json.loads(route.calls.last.request.content)["temperature"] == 0.0

    @respx.mock
    def test_images_are_inlined_as_data_uris(self, client):
        route = respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=completion("ok")))
        pixels = b"\x89PNG\r\n\x1a\n fake page render"

        client.analyze_image(pixels, "Transcribe this page.")

        content = json.loads(route.calls.last.request.content)["messages"][0]["content"]
        assert content[0] == {"type": "text", "text": "Transcribe this page."}
        assert content[1]["type"] == "image_url"

        url = content[1]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")
        assert base64.b64decode(url.split(",", 1)[1]) == pixels

    @respx.mock
    def test_multiple_pages_travel_in_one_request(self, client):
        route = respx.post(ENDPOINT).mock(
            return_value=httpx.Response(200, json=completion('{"field":"a","value":"b","confidence":1}'))
        )

        client.analyze_document(
            Extracted,
            "Extract the fields.",
            system="You are an extraction agent.",
            text="page text",
            images=[ImageContent(data=b"one"), ImageContent(data=b"two")],
        )

        messages = json.loads(route.calls.last.request.content)["messages"]
        assert messages[0]["role"] == "system"
        parts = messages[1]["content"]
        assert sum(1 for part in parts if part["type"] == "image_url") == 2
        assert "page text" in parts[0]["text"]

    @respx.mock
    def test_usage_and_latency_are_recorded(self, client):
        respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=completion("text")))

        response = client.generate([Message(role="user", content="x")])

        assert response.prompt_tokens == 120
        assert response.completion_tokens == 30
        assert response.total_tokens == 150
        assert response.latency_ms is not None


class TestStructuredOutput:
    @respx.mock
    def test_clean_json_is_parsed(self, client):
        respx.post(ENDPOINT).mock(
            return_value=httpx.Response(
                200, json=completion('{"field":"pan","value":"ABCDE1234F","confidence":0.95}')
            )
        )

        result = client.generate_structured(
            [Message(role="user", content="extract")], Extracted
        )

        assert result.data.value == "ABCDE1234F"
        assert result.attempts == 1

    @respx.mock
    def test_json_wrapped_in_prose_and_fences_is_recovered(self, client):
        wrapped = (
            "Here is what I found:\n"
            "```json\n"
            '{"field": "pan", "value": "ABCDE1234F", "confidence": 0.9}\n'
            "```\n"
            "Let me know if you need more."
        )
        respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=completion(wrapped)))

        result = client.generate_structured([Message(role="user", content="x")], Extracted)

        assert result.data.value == "ABCDE1234F"

    @respx.mock
    def test_invalid_output_triggers_a_correction_turn(self, client):
        route = respx.post(ENDPOINT).mock(
            side_effect=[
                # Missing a required field.
                httpx.Response(200, json=completion('{"field": "pan"}')),
                httpx.Response(
                    200, json=completion('{"field":"pan","value":"ABCDE1234F","confidence":0.9}')
                ),
            ]
        )

        result = client.generate_structured([Message(role="user", content="x")], Extracted)

        assert result.data.value == "ABCDE1234F"
        assert result.attempts == 2

        # The retry must name the problem without resending the document.
        second = json.loads(route.calls[1].request.content)["messages"]
        correction = second[-1]["content"]
        assert "could not be used" in correction
        assert "value" in correction

    @respx.mock
    def test_the_correction_does_not_repeat_the_document(self, client):
        respx.post(ENDPOINT).mock(
            side_effect=[
                httpx.Response(200, json=completion("not json at all")),
                httpx.Response(
                    200, json=completion('{"field":"a","value":"b","confidence":0.5}')
                ),
            ]
        )
        route = respx.routes[0]

        client.analyze_document(Extracted, "Extract.", text="A" * 5000)

        first = len(route.calls[0].request.content)
        second = len(route.calls[1].request.content)
        # The conversation grows by the correction, not by another copy of a
        # 5000-character document.
        assert second - first < 3000

    @respx.mock
    def test_persistent_garbage_raises_rather_than_guessing(self, client):
        respx.post(ENDPOINT).mock(
            return_value=httpx.Response(200, json=completion("I cannot answer that."))
        )

        with pytest.raises(StructuredOutputError) as error:
            client.generate_structured([Message(role="user", content="x")], Extracted)

        assert error.value.attempts == 3
        assert error.value.code == "LLM_INVALID_OUTPUT"

    @respx.mock
    def test_out_of_range_confidence_is_rejected_not_clamped_silently(self, client):
        # The schema constrains confidence; a model returning 5.0 must be
        # corrected rather than have the value quietly accepted.
        respx.post(ENDPOINT).mock(
            side_effect=[
                httpx.Response(
                    200, json=completion('{"field":"a","value":"b","confidence":5.0}')
                ),
                httpx.Response(
                    200, json=completion('{"field":"a","value":"b","confidence":0.8}')
                ),
            ]
        )

        result = client.generate_structured([Message(role="user", content="x")], Extracted)

        assert result.data.confidence == 0.8
        assert result.attempts == 2


class TestJsonMode:
    @respx.mock
    def test_json_mode_is_requested_for_structured_calls(self, client):
        route = respx.post(ENDPOINT).mock(
            return_value=httpx.Response(
                200, json=completion('{"field":"a","value":"b","confidence":0.5}')
            )
        )

        client.generate_structured([Message(role="user", content="x")], Extracted)

        assert json.loads(route.calls.last.request.content)["response_format"] == {
            "type": "json_object"
        }

    @respx.mock
    def test_a_server_rejecting_json_mode_is_retried_without_it(self, client):
        route = respx.post(ENDPOINT).mock(
            side_effect=[
                httpx.Response(400, text='{"error":"response_format is not supported"}'),
                httpx.Response(
                    200, json=completion('{"field":"a","value":"b","confidence":0.5}')
                ),
            ]
        )

        result = client.generate_structured([Message(role="user", content="x")], Extracted)

        assert result.data.value == "b"
        assert "response_format" not in json.loads(route.calls[1].request.content)
        # The capability is remembered, so every later call skips it too.
        assert client._supports_json_mode is False

    @respx.mock
    def test_plain_generation_does_not_ask_for_json_mode(self, client):
        route = respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=completion("hi")))

        client.generate([Message(role="user", content="x")])

        assert "response_format" not in json.loads(route.calls.last.request.content)


class TestFailureHandling:
    @respx.mock
    def test_server_errors_are_retried(self, client):
        route = respx.post(ENDPOINT).mock(
            side_effect=[
                httpx.Response(503, text="loading model"),
                httpx.Response(500, text="cuda oom"),
                httpx.Response(200, json=completion("recovered")),
            ]
        )

        response = client.generate([Message(role="user", content="x")])

        assert response.text == "recovered"
        assert len(route.calls) == 3
        assert response.attempts == 3

    @respx.mock
    def test_rate_limiting_is_retried(self, client):
        respx.post(ENDPOINT).mock(
            side_effect=[
                httpx.Response(429, text="slow down"),
                httpx.Response(200, json=completion("ok")),
            ]
        )

        assert client.generate([Message(role="user", content="x")]).text == "ok"

    @respx.mock
    def test_exhausted_retries_raise_a_timeout_error(self, client):
        respx.post(ENDPOINT).mock(return_value=httpx.Response(503, text="still loading"))

        with pytest.raises(LLMTimeoutError) as error:
            client.generate([Message(role="user", content="x")])

        assert error.value.code == "LLM_TIMEOUT"

    @respx.mock
    def test_connection_failures_are_retried_then_surfaced(self, client):
        respx.post(ENDPOINT).mock(side_effect=httpx.ConnectError("connection refused"))

        with pytest.raises(LLMTimeoutError):
            client.generate([Message(role="user", content="x")])

    @respx.mock
    def test_a_timeout_is_retried_then_surfaced(self, client):
        respx.post(ENDPOINT).mock(side_effect=httpx.ReadTimeout("too slow"))

        with pytest.raises(LLMTimeoutError):
            client.generate([Message(role="user", content="x")])

    @respx.mock
    def test_authentication_failure_is_not_retried(self, client):
        # A bad key will still be bad in 200ms; retrying wastes the budget and
        # hides the real cause.
        route = respx.post(ENDPOINT).mock(return_value=httpx.Response(401, text="bad key"))

        with pytest.raises(LLMTransportError) as error:
            client.generate([Message(role="user", content="x")])

        assert len(route.calls) == 1
        assert "401" in str(error.value)

    @respx.mock
    def test_an_empty_choice_list_does_not_crash(self, client):
        respx.post(ENDPOINT).mock(
            return_value=httpx.Response(200, json={"model": "m", "choices": []})
        )

        assert client.generate([Message(role="user", content="x")]).text == ""
