"""Preflight for a model endpoint.

Run this before pointing the pipeline at a new model. It answers, in order, the
questions that decide whether the endpoint is usable at all:

  1. Is it reachable, and what does it serve?
  2. Does plain completion work?
  3. Does it honour JSON mode, or must we fall back?
  4. Can it produce a schema-valid object?
  5. Can it read an image? (Extraction from scans depends entirely on this.)
  6. How slow is it, and how many tokens does a realistic page cost?

Each check prints a verdict and keeps going, because knowing that vision fails
while text works is more useful than stopping at the first problem.

    python scripts/check_endpoint.py
    python scripts/check_endpoint.py --base-url http://localhost:8001/v1 --model Qwen3-VL-8B-Instruct
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from typing import Any

import httpx
from pydantic import BaseModel, Field

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.llm.client import ImageContent, LLMError, Message  # noqa: E402
from app.llm.qwen import QwenLLMClient  # noqa: E402

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"

# The value the vision check hides in a generated image. Deliberately shaped
# like a PAN so the check exercises the same characters real extraction needs:
# a model that reads it as ABCDE1Z34F is a model that will misread identifiers.
VISION_SECRET = "ABCDE1234F"


class ProbeExtraction(BaseModel):
    """A miniature of the real extraction contract."""

    field: str
    value: str
    confidence: float = Field(ge=0.0, le=1.0)


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, verdict: str, name: str, detail: str = "") -> None:
        self.rows.append((verdict, name, detail))
        marker = {PASS: "  ok  ", FAIL: " FAIL ", WARN: " warn "}[verdict]
        print(f"[{marker}] {name}")
        if detail:
            for line in detail.splitlines():
                print(f"          {line}")

    @property
    def failed(self) -> bool:
        return any(verdict == FAIL for verdict, _, _ in self.rows)


def check_reachable(base_url: str, api_key: str, report: Report) -> list[str]:
    """List served models. A wrong served-name is the most common mistake."""
    try:
        response = httpx.get(
            f"{base_url.rstrip('/')}/models",
            headers={"authorization": f"Bearer {api_key}"},
            timeout=15,
        )
    except httpx.HTTPError as exc:
        report.add(FAIL, "endpoint reachable", f"{type(exc).__name__}: {exc}")
        return []

    if response.status_code >= 400:
        report.add(
            WARN,
            "model listing",
            f"{response.status_code} from /models; some servers do not expose it",
        )
        return []

    try:
        names = [entry["id"] for entry in response.json().get("data", [])]
    except (ValueError, KeyError, TypeError):
        report.add(WARN, "model listing", "the response was not in the expected shape")
        return []

    # A single-model server lists one name; a gateway lists hundreds. Print a
    # sample rather than the catalogue.
    if len(names) > 6:
        detail = f"{len(names)} models available (gateway)"
    else:
        detail = f"serving: {', '.join(names) or 'nothing listed'}"
    report.add(PASS, "endpoint reachable", detail)
    return names


def check_model_name(model: str, served: list[str], report: Report) -> None:
    if not served:
        return
    if model in served:
        report.add(PASS, "model name matches", model)
    else:
        report.add(
            FAIL,
            "model name matches",
            f"LLM_MODEL is {model!r} but the server serves {served}.\n"
            "Set LLM_MODEL to one of those, or --served-model-name on the server.",
        )


def check_completion(client: QwenLLMClient, report: Report) -> None:
    try:
        started = time.perf_counter()
        response = client.generate(
            [Message(role="user", content="Reply with the single word: ready")],
            max_tokens=16,
        )
        elapsed = time.perf_counter() - started
    except LLMError as exc:
        report.add(FAIL, "text completion", f"{type(exc).__name__}: {exc}")
        return

    text = response.text.strip()
    detail = f"{elapsed:.1f}s, {response.total_tokens or '?'} tokens, replied {text[:60]!r}"
    report.add(PASS if text else FAIL, "text completion", detail)


def check_structured(client: QwenLLMClient, report: Report) -> None:
    try:
        result = client.analyze_document(
            ProbeExtraction,
            "Extract the PAN from this document.",
            system="You are a high-precision extraction agent. Return JSON only.",
            text="Name: Ravi Kumar\nPAN: ABCDE1234F\nDate of Birth: 12/04/1998",
        )
    except LLMError as exc:
        report.add(FAIL, "structured output", f"{type(exc).__name__}: {exc}")
        return

    detail = (
        f"attempts: {result.attempts}, "
        f"json_mode: {'yes' if client._supports_json_mode else 'rejected, fell back'}, "
        f"value: {result.data.value!r}"
    )
    if result.data.value.replace(" ", "").upper() == VISION_SECRET:
        report.add(PASS, "structured output", detail)
    else:
        report.add(
            WARN,
            "structured output",
            detail + "\nSchema-valid but the value is wrong; check the prompt or the model.",
        )


def make_probe_image() -> bytes:
    """A synthetic identity card, rendered large enough to be legible."""
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (1000, 620), "white")
    draw = ImageDraw.Draw(image)
    try:
        heading = ImageFont.load_default(size=40)
        body = ImageFont.load_default(size=32)
    except TypeError:  # older Pillow
        heading = body = ImageFont.load_default()

    draw.text((50, 40), "INCOME TAX DEPARTMENT", fill="black", font=heading)
    draw.text((50, 100), "PERMANENT ACCOUNT NUMBER", fill="black", font=body)
    draw.text((50, 200), "Name: RAVI KUMAR", fill="black", font=body)
    draw.text((50, 260), f"PAN: {VISION_SECRET}", fill="black", font=body)
    draw.text((50, 320), "Date of Birth: 12/04/1998", fill="black", font=body)
    draw.rectangle([30, 20, 970, 400], outline="black", width=3)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def check_vision(client: QwenLLMClient, report: Report) -> None:
    """The check that matters most: every scanned document depends on it."""
    try:
        pixels = make_probe_image()
    except Exception as exc:  # noqa: BLE001
        report.add(WARN, "vision", f"could not build the probe image: {exc}")
        return

    try:
        started = time.perf_counter()
        text = client.analyze_image(
            ImageContent(data=pixels),
            "Transcribe all visible text from this document image exactly as it appears. "
            "Return the transcription only.",
        )
        elapsed = time.perf_counter() - started
    except LLMError as exc:
        report.add(
            FAIL,
            "vision",
            f"{type(exc).__name__}: {exc}\n"
            "Scanned PDFs and photographs cannot be processed without this.",
        )
        return

    normalised = (text or "").upper().replace(" ", "")
    if VISION_SECRET in normalised:
        report.add(PASS, "vision", f"{elapsed:.1f}s, read the identifier correctly")
    elif text.strip():
        report.add(
            FAIL,
            "vision",
            f"{elapsed:.1f}s, the model responded but misread the identifier.\n"
            f"expected {VISION_SECRET} in: {text.strip()[:160]!r}",
        )
    else:
        report.add(FAIL, "vision", "the model returned nothing for the image")


def check_page_cost(client: QwenLLMClient, report: Report) -> None:
    """A realistic page, to size the token budget before a real run."""
    page = (
        "AADHAAR - UNIQUE IDENTIFICATION AUTHORITY OF INDIA\n"
        + "Name: Ravi Kumar\nDate of Birth: 12/04/1998\nGender: Male\n"
        + "Aadhaar: 2345 6789 0124\nAddress: 12 MG Road, Bengaluru 560001\n"
        + ("Additional clause text. " * 200)
    )
    try:
        started = time.perf_counter()
        result = client.analyze_document(
            ProbeExtraction,
            "Extract the applicant name.",
            system="Return JSON only.",
            text=page,
        )
        elapsed = time.perf_counter() - started
    except LLMError as exc:
        report.add(WARN, "page cost", f"could not measure: {type(exc).__name__}: {exc}")
        return

    prompt_tokens = result.prompt_tokens or 0
    report.add(
        PASS,
        "page cost",
        f"{elapsed:.1f}s for a ~{len(page)} character page, "
        f"{prompt_tokens} prompt tokens, {result.completion_tokens or 0} completion",
    )



def discover_free_vision_models(base_url: str, api_key: str, limit: int = 12) -> list[str]:
    """Find candidate free models that accept image input.

    Free tiers rotate without notice, and a model that vanishes takes the
    pipeline with it. Rediscovering candidates has to be one command, not an
    afternoon of reading a pricing page.
    """
    try:
        response = httpx.get(
            f"{base_url.rstrip('/')}/models",
            headers={"authorization": f"Bearer {api_key}"},
            timeout=30,
        )
        response.raise_for_status()
        catalogue = response.json().get("data", [])
    except (httpx.HTTPError, ValueError) as exc:
        print(f"could not list models: {type(exc).__name__}: {exc}")
        return []

    candidates = []
    for entry in catalogue:
        identifier = entry.get("id", "")
        modalities = (entry.get("architecture") or {}).get("input_modalities") or []
        pricing = entry.get("pricing") or {}
        is_free = identifier.endswith(":free") or (
            _as_float(pricing.get("prompt")) == 0.0 and _as_float(pricing.get("completion")) == 0.0
        )
        if is_free and "image" in modalities:
            candidates.append(identifier)
    return sorted(candidates)[:limit]


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def probe_candidate(base_url: str, api_key: str, model: str, timeout: float) -> dict[str, Any]:
    """Two calls: can it answer at all, and can it read an identifier off an image."""
    client = QwenLLMClient(
        base_url=base_url, model=model, api_key=api_key, timeout=timeout, max_retries=1
    )
    result: dict[str, Any] = {"model": model, "text": False, "vision": False, "note": ""}
    try:
        reply = client.generate(
            [Message(role="user", content="Reply with the single word: ready")], max_tokens=16
        )
        result["text"] = bool(reply.text.strip())
    except LLMError as exc:
        result["note"] = f"{type(exc).__name__}: {str(exc)[:90]}"
        client.close()
        return result

    try:
        transcription = client.analyze_image(
            ImageContent(data=make_probe_image()),
            "Transcribe all visible text from this document image exactly as it appears.",
        )
        result["vision"] = VISION_SECRET in (transcription or "").upper().replace(" ", "")
        if not result["vision"]:
            result["note"] = f"misread: {(transcription or '').strip()[:70]!r}"
    except LLMError as exc:
        result["note"] = f"vision {type(exc).__name__}: {str(exc)[:90]}"
    client.close()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Check a model endpoint before using it.")
    parser.add_argument("--base-url", default=settings.LLM_BASE_URL)
    parser.add_argument("--model", default=settings.LLM_MODEL)
    parser.add_argument("--api-key", default=settings.LLM_API_KEY)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--skip-vision", action="store_true",
                        help="skip the image check for a text-only model")
    parser.add_argument("--json", action="store_true", help="emit machine-readable results")
    parser.add_argument(
        "--discover",
        action="store_true",
        help="probe every free vision model the endpoint offers and rank them",
    )
    args = parser.parse_args()

    if settings.LLM_USE_MOCK:
        print(
            "LLM_USE_MOCK is true, so the application would not call this endpoint.\n"
            "Set LLM_USE_MOCK=false before running a real analysis.\n"
        )

    if args.discover:
        return _discover(args)

    print(f"Checking {args.base_url} for model {args.model}\n")

    report = Report()
    served = check_reachable(args.base_url, args.api_key, report)
    check_model_name(args.model, served, report)

    client = QwenLLMClient(
        base_url=args.base_url,
        model=args.model,
        api_key=args.api_key,
        timeout=args.timeout,
        max_retries=2,
    )

    check_completion(client, report)
    check_structured(client, report)
    if not args.skip_vision:
        check_vision(client, report)
    check_page_cost(client, report)
    client.close()

    print()
    if args.json:
        payload: list[dict[str, Any]] = [
            {"check": name, "verdict": verdict, "detail": detail}
            for verdict, name, detail in report.rows
        ]
        print(json.dumps(payload, indent=2))

    if report.failed:
        print("Endpoint is NOT ready. Fix the failures above before running an analysis.")
        return 1

    print("Endpoint is ready. Run: make eval-live")
    return 0

def _discover(args: argparse.Namespace) -> int:
    """Rank the free vision models this endpoint currently offers."""
    print(f"Discovering free vision models at {args.base_url}")
    print()
    candidates = discover_free_vision_models(args.base_url, args.api_key)
    if not candidates:
        print("No free models advertising image input were found.")
        return 1

    print(f"{len(candidates)} candidate(s); probing each with two calls")
    print()
    results = [probe_candidate(args.base_url, args.api_key, m, args.timeout) for m in candidates]

    usable = [r for r in results if r["text"] and r["vision"]]
    partial = [r for r in results if r["text"] and not r["vision"]]
    broken = [r for r in results if not r["text"]]

    for label, group in (("USABLE", usable), ("TEXT ONLY", partial), ("UNAVAILABLE", broken)):
        if not group:
            continue
        print(f"{label}:")
        for row in group:
            note = f"  ({row['note']})" if row["note"] else ""
            print(f"    {row['model']}{note}")
        print()

    if usable:
        print(f"Set LLM_MODEL={usable[0]['model']}")
        return 0

    print("No free model here can both answer and read an image.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
