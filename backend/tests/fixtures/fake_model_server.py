"""A stub OpenAI-compatible server, for testing the live code path.

The mock client bypasses HTTP entirely, which means the wire format, the
retry logic and the endpoint configuration never run in the test suite. This
serves the same stubbed answers over a real `/v1/chat/completions`, so
`LDAI_LIVE_LLM=1` can be exercised end to end on a machine with no GPU.

It is a test double and nothing more. It does not read images, and it says so
rather than inventing a transcription — which is exactly what makes it useful
for checking that the preflight script detects a broken vision endpoint.

    python -m tests.fixtures.fake_model_server --port 8001
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from app.llm.mock import MockLLMClient

MODEL_NAME = "Qwen3-VL-8B-Instruct"

# Which stubbed handler a request wants, inferred from the JSON schema the
# prompt carries. The server never sees the Pydantic class, only its shape.
SCHEMA_SIGNATURES: list[tuple[str, set[str]]] = [
    ("ClassificationResult", {"document_type", "is_readable"}),
    ("ExtractionResult", {"fields", "document_type"}),
    ("DiscrepancyAssessment", {"classification", "severity"}),
    ("VerificationResult", {"verified", "evidence_quality"}),
    ("QAResult", {"passed", "errors"}),
    ("ProfileAgentOutput", {"fields"}),
    ("MappedSection", {"content"}),
]

_SCHEMA_IN_PROMPT = re.compile(r"schema:\s*(\{.*)\s*$", re.IGNORECASE | re.DOTALL)
_LABELLED = re.compile(r"^\s*([A-Za-z][A-Za-z /.'-]{1,40}?)\s*[:|\-]\s*(.+?)\s*$", re.MULTILINE)


class ChatMessage(BaseModel):
    role: str
    content: Any


class ChatRequest(BaseModel):
    model: str = MODEL_NAME
    messages: list[ChatMessage]
    temperature: float = 0.0
    max_tokens: int | None = None
    response_format: dict[str, Any] | None = None


def create_app(*, supports_json_mode: bool = True, vision: bool = False) -> FastAPI:
    """Build the stub.

    ``supports_json_mode=False`` reproduces a server that rejects
    ``response_format``, which is how the client's fallback path gets tested.
    """
    app = FastAPI(title="Fake model server")
    client = MockLLMClient()

    @app.get("/v1/models")
    def list_models() -> dict[str, Any]:
        return {"object": "list", "data": [{"id": MODEL_NAME, "object": "model"}]}

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/chat/completions")
    def completions(request: ChatRequest) -> Any:
        if request.response_format and not supports_json_mode:
            from fastapi.responses import JSONResponse

            return JSONResponse(
                status_code=400,
                content={"error": "response_format is not supported by this server"},
            )

        text_parts: list[str] = []
        has_image = False
        for message in request.messages:
            if isinstance(message.content, str):
                text_parts.append(message.content)
            elif isinstance(message.content, list):
                for part in message.content:
                    if part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                    elif part.get("type") == "image_url":
                        has_image = True

        prompt = "\n".join(text_parts)

        if has_image and not _wants_json(prompt):
            # A transcription request. Refusing plainly beats fabricating text
            # that would look like a working vision model.
            content = "[stub server] no vision model is attached; the image was not read"
        elif not _wants_json(prompt):
            content = "ready"
        else:
            content = json.dumps(_answer(client, prompt))

        return {
            "id": "chatcmpl-stub",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": MODEL_NAME,
            "choices": [
                {"index": 0, "message": {"role": "assistant", "content": content},
                 "finish_reason": "stop"}
            ],
            "usage": {
                "prompt_tokens": max(1, len(prompt) // 4),
                "completion_tokens": max(1, len(content) // 4),
                "total_tokens": max(2, (len(prompt) + len(content)) // 4),
            },
        }

    return app


def _wants_json(prompt: str) -> bool:
    return "schema" in prompt.lower() and "{" in prompt


def _answer(client: MockLLMClient, prompt: str) -> Any:
    """Produce the stubbed payload the requested schema expects."""
    schema = _requested_schema(prompt)
    properties = set((schema or {}).get("properties", {}))

    for name, signature in SCHEMA_SIGNATURES:
        if signature <= properties:
            return client._payload_for(name, prompt)  # noqa: SLF001

    # The preflight probe: a small {field, value, confidence} object. Answer it
    # by finding the label the prompt asked about in the document text.
    if {"field", "value", "confidence"} <= properties:
        return _probe_answer(prompt)

    return {}


def _requested_schema(prompt: str) -> dict[str, Any] | None:
    match = _SCHEMA_IN_PROMPT.search(prompt)
    if not match:
        return None
    candidate = match.group(1).strip()
    # The schema is the first balanced object; anything after it is prose.
    depth = 0
    for index, character in enumerate(candidate):
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(candidate[: index + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _probe_answer(prompt: str) -> dict[str, Any]:
    body = prompt.split("DOCUMENT TEXT:", 1)[-1]
    # Everything from the schema instruction onward is the prompt talking to
    # itself; scanning it finds "schema: {...}" and answers with the schema.
    body = re.split(r"Return JSON matching this schema", body, maxsplit=1)[0]
    pairs = {label.strip().lower(): value.strip() for label, value in _LABELLED.findall(body)}
    # Only the instruction says which field is wanted. Searching the whole
    # prompt would match every label, since the document is part of it.
    instruction = prompt.split("DOCUMENT TEXT:", 1)[0].lower()

    # Longest label first, so "date of birth" beats "date".
    for label in sorted(pairs, key=len, reverse=True):
        if label in instruction:
            return {"field": label.replace(" ", "_"), "value": pairs[label], "confidence": 0.9}
    return {"field": "unknown", "value": "NOT_FOUND", "confidence": 0.0}


app = create_app()


if __name__ == "__main__":
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="Run the stub model server.")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-json-mode", action="store_true",
                        help="reject response_format, to exercise the client fallback")
    args = parser.parse_args()

    uvicorn.run(
        create_app(supports_json_mode=not args.no_json_mode),
        host=args.host,
        port=args.port,
        log_level="warning",
    )
