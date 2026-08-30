"""Getting a schema-valid object out of a text-completion model.

Models wrap JSON in prose, in code fences, or emit a trailing comma. None of
that is a reason to fail a case, and none of it is a reason to accept a guess
either. The sequence is: pull the most plausible JSON out of the text, validate
it against the Pydantic schema, and on failure hand the model a compact
correction naming the exact problem.

What is never done is repairing values. Structural repair is fine; inventing a
field the model omitted is not.
"""

from __future__ import annotations

import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

TModel = TypeVar("TModel", bound=BaseModel)

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_TRAILING_COMMA = re.compile(r",\s*([}\]])")


def extract_json(text: str) -> Any:
    """Find and parse the JSON document inside a model response."""
    if not text or not text.strip():
        raise ValueError("the response was empty")

    candidates: list[str] = []
    fenced = _FENCE.search(text)
    if fenced:
        candidates.append(fenced.group(1))
    candidates.append(text)

    stripped = _balanced_span(text)
    if stripped:
        candidates.append(stripped)

    last_error: Exception | None = None
    for candidate in candidates:
        cleaned = candidate.strip()
        if not cleaned:
            continue
        for attempt in (cleaned, _TRAILING_COMMA.sub(r"\1", cleaned)):
            try:
                return json.loads(attempt)
            except json.JSONDecodeError as exc:
                last_error = exc
    raise ValueError(f"no parseable JSON in the response: {last_error}")


def _balanced_span(text: str) -> str | None:
    """The first balanced {...} or [...] block, ignoring braces inside strings."""
    start = None
    opener = closer = ""
    for index, char in enumerate(text):
        if char in "{[":
            start = index
            opener = char
            closer = "}" if char == "{" else "]"
            break
    if start is None:
        return None

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def validate_payload(payload: Any, schema: type[TModel]) -> TModel:
    return schema.model_validate(payload)


def parse_structured(text: str, schema: type[TModel]) -> TModel:
    return validate_payload(extract_json(text), schema)


def describe_validation_error(error: ValidationError) -> str:
    """A short, model-readable account of what was wrong."""
    lines = []
    for item in error.errors()[:8]:
        location = ".".join(str(part) for part in item["loc"]) or "(root)"
        lines.append(f"- {location}: {item['msg']}")
    return "\n".join(lines)


def correction_message(problem: str, schema: type[BaseModel]) -> str:
    """The follow-up turn sent after unusable output.

    Deliberately compact: repeating the document would multiply the token cost
    of every retry, and the model still has it in context.
    """
    return (
        "Your previous response could not be used.\n"
        f"Problem:\n{problem}\n\n"
        "Return only a single JSON object that satisfies this schema:\n"
        f"{schema_summary(schema)}\n"
        "No prose, no code fences, no explanation. Do not invent values that "
        "were not in the document; use NOT_FOUND where a field is absent."
    )


def schema_summary(schema: type[BaseModel]) -> str:
    """Compact JSON-Schema for prompting, with the noise removed."""
    raw = schema.model_json_schema()
    return json.dumps(_prune(raw), separators=(",", ":"))


def _prune(node: Any) -> Any:
    """Drop keys that cost tokens without constraining the answer."""
    drop = {"title", "description", "examples", "default", "additionalProperties"}
    if isinstance(node, dict):
        return {key: _prune(value) for key, value in node.items() if key not in drop}
    if isinstance(node, list):
        return [_prune(item) for item in node]
    return node
