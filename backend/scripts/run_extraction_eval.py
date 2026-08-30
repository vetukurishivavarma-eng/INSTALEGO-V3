"""Measure where extraction degrades, one document and one condition at a time.

``run_evaluation.py`` runs whole cases through the API and asks whether the
right discrepancy was found. This runs a single document through the per-document
pipeline — parse, transcribe, classify, extract — and asks a narrower question:
was the page read correctly, and if it was not, did the system admit it.

Only that pipeline is run. The profile builder, the rule engine and the evidence
verifier are all skipped, which takes a variant from roughly a dozen model calls
to three and is what makes a sweep affordable on a rate-limited free endpoint.

    python scripts/run_extraction_eval.py                       # stub, plumbing only
    python scripts/run_extraction_eval.py --live --core         # the affordable sweep
    python scripts/run_extraction_eval.py --live --resume       # continue an interrupted one
    python scripts/run_extraction_eval.py --live --documents aadhaar \
        --degradations clean blur_severe
    python scripts/run_extraction_eval.py --render-only out/    # write the fixtures, call nothing

Results are written after every variant, so an interrupted run keeps its work
and ``--resume`` picks up where the quota ran out.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

DEFAULT_OUT = BACKEND / "var" / "extraction-eval.json"


def configure(live: bool) -> None:
    """Set the environment before anything imports application settings."""
    root = Path(tempfile.gettempdir()) / "ldai-extraction-eval"
    root.mkdir(parents=True, exist_ok=True)

    os.environ.update(
        {
            "ENVIRONMENT": "test",
            "LOG_LEVEL": "WARNING",
            "DATABASE_URL": f"sqlite+pysqlite:///{(root / 'extraction.db').as_posix()}",
            "STORAGE_BACKEND": "local",
            "STORAGE_LOCAL_ROOT": str(root / "documents"),
            "TASK_QUEUE_BACKEND": "inline",
            "AUTH_ENABLED": "false",
            "DEFAULT_BANK_ID": "bank_a",
        }
    )
    # As in run_evaluation.py: LLM_BASE_URL, LLM_MODEL and LLM_API_KEY are left
    # to .env or the caller. Environment variables outrank .env, so defaulting
    # them here would silently shadow the configured endpoint.
    os.environ["LLM_USE_MOCK"] = "false" if live else "true"


def load_existing(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("variants", {})
    except (ValueError, OSError):
        return {}


def write_results(path: Path, variants: dict[str, Any], model: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "model": model,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "variants": variants,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def observe(db, document) -> dict[str, Any]:
    """Read back everything the pipeline recorded about one document."""
    from app.models.extraction import FieldValue

    pages = list(document.pages)
    confidences = [page.ocr_confidence for page in pages if page.ocr_confidence is not None]

    fields: dict[str, list[dict[str, Any]]] = {}
    rows = db.query(FieldValue).filter(FieldValue.document_id == document.id).all()
    for row in rows:
        fields.setdefault(row.field_name, []).append(
            {
                "original": row.original_value,
                "normalized": row.normalized_value,
                "confidence": row.confidence,
                "page": row.page_number,
            }
        )

    return {
        "document_type": str(document.document_type or ""),
        "is_readable": bool(document.is_readable),
        "quality_status": str(document.quality_status or ""),
        "quality_flags": list(document.quality_flags or []),
        "ocr_confidence": min(confidences) if confidences else None,
        "text_length": sum(len(page.text or "") for page in pages),
        "fields": fields,
    }


def run_one(variant, directory: Path) -> dict[str, Any]:
    """Push one degraded document all the way through the document pipeline."""
    from app.db import SessionLocal
    from app.schemas.case import CaseCreate
    from app.services import case_service, document_service
    from app.workflows.extraction_workflow import process_document

    path = variant.build(directory)
    session = SessionLocal()
    try:
        case = case_service.create_case(
            session, CaseCreate(bank_id="bank_a", applicant_name="Ravi Kumar")
        )
        upload = document_service.ingest(
            session, case, filename=path.name, content=path.read_bytes()
        )
        if not upload.accepted:
            return {"error": f"upload rejected: {upload.error_code} {upload.error_detail}"}

        document = document_service.get_document(session, upload.document_id)
        outcome = process_document(session, document)
        session.commit()

        observed = observe(session, document)
        if not outcome.ok:
            observed["error"] = f"{outcome.error_code}: {outcome.error_detail}"
        return observed
    finally:
        session.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure extraction accuracy under image degradation."
    )
    parser.add_argument("--live", action="store_true", help="use the configured endpoint")
    parser.add_argument("--core", action="store_true",
                        help="the affordable sweep: 3 layouts x 9 conditions")
    parser.add_argument("--documents", nargs="*", help="document ids (default: all)")
    parser.add_argument("--degradations", nargs="*", help="condition names (default: all)")
    parser.add_argument("--format", default="jpg", choices=["jpg", "png", "pdf"],
                        help="how the degraded page is delivered")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--resume", action="store_true", help="skip variants already recorded")
    parser.add_argument("--limit", type=int, help="stop after this many variants")
    parser.add_argument("--render-only", type=Path, metavar="DIR",
                        help="write the fixtures to DIR and exit, calling no model")
    parser.add_argument("--rescore", action="store_true",
                        help="re-apply the scoring rules to a previous run's raw values "
                             "and rewrite the report, calling no model")
    args = parser.parse_args()

    # A live sweep is long and mostly spent waiting. Block-buffered output
    # would hold every line until the end, which is what makes an interrupted
    # run look like a hung one.
    sys.stdout.reconfigure(line_buffering=True)

    configure(args.live)

    from app.config import settings
    from app.db import init_db
    from tests.evaluation.extraction_cases import core_matrix, matrix
    from tests.evaluation.extraction_runner import aggregate, format_report, score_variant

    selected = (
        core_matrix(args.format)
        if args.core
        else matrix(
            tuple(args.documents) if args.documents else None,
            tuple(args.degradations) if args.degradations else None,
            args.format,
        )
    )
    if args.limit:
        selected = selected[: args.limit]

    if args.render_only:
        directory = args.render_only
        for variant in selected:
            written = variant.build(directory)
            print(f"  {variant.variant_id:<30} -> {written.name}")
        print(f"\n{len(selected)} fixtures written to {directory}")
        return 0

    if args.rescore:
        return rescore(args.out, {variant.variant_id: variant for variant in selected})

    init_db()
    model = settings.LLM_MODEL if args.live else "stub"
    recorded = load_existing(args.out) if args.resume else {}

    print(f"model:    {model}")
    print(f"variants: {len(selected)} ({args.format})")
    if recorded:
        print(f"resuming, {len(recorded)} already recorded")
    print()

    for variant in selected:
        if variant.variant_id in recorded:
            print(f"  {variant.variant_id:<30} skipped (already recorded)")
            continue

        directory = Path(tempfile.mkdtemp(prefix="ldai-hard-"))
        started = time.perf_counter()
        try:
            observed = run_one(variant, directory)
        except Exception as exc:  # noqa: BLE001 - one bad variant must not lose the rest
            observed = {"error": f"{type(exc).__name__}: {exc}"}
        observed["seconds"] = round(time.perf_counter() - started, 1)

        score = score_variant(variant, observed)
        recorded[variant.variant_id] = _record(score)
        write_results(args.out, recorded, model)

        counted = score.counted
        mark = "ok " if score.honest else "BAD"
        print(
            f"  {variant.variant_id:<30} {mark} {observed['seconds']:>5.1f}s  "
            f"correct={counted['correct']} wrong={counted['wrong']} "
            f"missing={counted['missing']} "
            f"type={score.classified_type or '-'} "
            f"flagged={'y' if score.flagged else 'n'}"
        )
        if score.error:
            print(f"        ERROR {score.error}")

    scores = [_restore(data) for data in recorded.values()]
    print(format_report(scores))
    print(f"\nresults written to {args.out}")

    totals = aggregate(scores)
    # A run "fails" only on silent wrongness or an error. A missing field under
    # a severe degradation is the system behaving as intended.
    return 1 if totals.silently_wrong or totals.errors else 0


def rescore(path: Path, variants: dict[str, Any]) -> int:
    """Re-apply the scoring rules to what a previous run already read.

    A change to how a field is compared should not cost a day's quota to
    evaluate. Only the reported row per field is stored rather than every row
    the extractor produced, so this re-derives verdicts from the values that
    were actually reported -- which is what the verdicts were based on anyway.
    """
    from tests.evaluation.extraction_runner import aggregate, format_report, score_variant

    stored = load_existing(path)
    if not stored:
        print(f"nothing to rescore in {path}")
        return 1

    payload = json.loads(path.read_text(encoding="utf-8"))
    scores = []
    for variant_id, data in stored.items():
        variant = variants.get(variant_id)
        if variant is None:
            scores.append(_restore(data))
            continue
        observed = {
            "document_type": data.get("classified_type", ""),
            "is_readable": data.get("is_readable", True),
            "quality_status": data.get("quality_status", ""),
            "quality_flags": data.get("quality_flags", []),
            "ocr_confidence": data.get("ocr_confidence"),
            "text_length": data.get("text_length", 0),
            "seconds": data.get("seconds", 0.0),
            "error": data.get("error"),
            "fields": {
                outcome["name"]: [
                    {
                        "original": outcome.get("got"),
                        "normalized": outcome.get("normalized"),
                        "confidence": outcome.get("confidence"),
                    }
                ]
                for outcome in data.get("fields", [])
                if outcome.get("got") is not None
            },
        }
        score = score_variant(variant, observed)
        stored[variant_id] = _record(score)
        scores.append(score)

    write_results(path, stored, payload.get("model", "unknown"))
    print(format_report(scores))
    print(f"\nrescored {len(scores)} variants, written to {path}")

    totals = aggregate(scores)
    return 1 if totals.silently_wrong or totals.errors else 0


def _record(score) -> dict[str, Any]:
    return {
        "variant_id": score.variant_id,
        "doc_id": score.doc_id,
        "degradation": score.degradation,
        "condition": score.condition,
        "expectation": score.expectation,
        "classified_type": score.classified_type,
        "type_correct": score.type_correct,
        "is_readable": score.is_readable,
        "quality_status": score.quality_status,
        "quality_flags": score.quality_flags,
        "ocr_confidence": score.ocr_confidence,
        "text_length": score.text_length,
        "seconds": score.seconds,
        "error": score.error,
        "fields": [
            {
                "name": outcome.name,
                "expected": outcome.expected,
                "verdict": outcome.verdict,
                "got": outcome.got,
                "normalized": outcome.normalized,
                "confidence": outcome.confidence,
                "note": outcome.note,
            }
            for outcome in score.fields
        ],
    }


def _restore(data: dict[str, Any]):
    """Rebuild a score from the results file, so a resumed run reports on the
    whole sweep rather than only the part it ran itself."""
    from tests.evaluation.extraction_runner import FieldOutcome, VariantScore

    score = VariantScore(
        variant_id=data["variant_id"],
        doc_id=data["doc_id"],
        degradation=data["degradation"],
        condition=data.get("condition", ""),
        expectation=data.get("expectation", "read"),
        classified_type=data.get("classified_type", ""),
        type_correct=data.get("type_correct", False),
        is_readable=data.get("is_readable", True),
        quality_status=data.get("quality_status", ""),
        quality_flags=data.get("quality_flags", []),
        ocr_confidence=data.get("ocr_confidence"),
        text_length=data.get("text_length", 0),
        seconds=data.get("seconds", 0.0),
        error=data.get("error"),
    )
    score.fields = [FieldOutcome(**outcome) for outcome in data.get("fields", [])]
    return score


if __name__ == "__main__":
    raise SystemExit(main())
