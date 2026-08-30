"""Run the evaluation cases and write results as they complete.

The pytest suite is right for the stub, where a whole run takes seconds. It is
the wrong shape for a live endpoint: a hundred throttled calls take a quarter
of an hour, pytest buffers its output until the last one lands, and anything
that interrupts the run loses every case that had already succeeded.

This runner writes each case to disk the moment it finishes and can resume, so
a long run against a rate-limited endpoint survives being interrupted, and a
partial result is still a result.

    python scripts/run_evaluation.py --live                 # every case
    python scripts/run_evaluation.py --live --cases 001 002 # a subset
    python scripts/run_evaluation.py --live --resume        # skip what is done
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

DEFAULT_OUT = BACKEND / "var" / "evaluation.json"


def configure(live: bool) -> None:
    """Set the environment before anything imports application settings."""
    import tempfile

    root = Path(tempfile.gettempdir()) / "ldai-eval"
    root.mkdir(parents=True, exist_ok=True)

    os.environ.update(
        {
            "ENVIRONMENT": "test",
            "LOG_LEVEL": "WARNING",
            "DATABASE_URL": f"sqlite+pysqlite:///{(root / 'eval.db').as_posix()}",
            "STORAGE_BACKEND": "local",
            "STORAGE_LOCAL_ROOT": str(root / "documents"),
            "TASK_QUEUE_BACKEND": "inline",
            "AUTH_ENABLED": "false",
            "DEFAULT_BANK_ID": "bank_a",
        }
    )
    # Live mode leaves LLM_BASE_URL, LLM_MODEL and LLM_API_KEY to .env or the
    # caller. Defaulting them here would shadow the configured endpoint, since
    # environment variables outrank .env in settings.
    os.environ["LLM_USE_MOCK"] = "false" if live else "true"


def load_existing(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("cases", {})
    except (ValueError, OSError):
        return {}


def prompt_versions() -> dict[str, str]:
    """The exact prompts this run was measured against.

    Without this a published number cannot be tied to the system that produced
    it. These results were quoted in the README for a day after both prompts
    had been rewritten underneath them, and only a diff of the prompt hashes
    showed it -- so the hashes are recorded here now.
    """
    from app.agents.base_agent import prompt_version

    names = ("classifier", "extractor", "profile_builder", "discrepancy_reasoner",
             "evidence_verifier", "report_mapper", "qa_agent", "encumbrance_reader")
    versions = {}
    for name in names:
        try:
            versions[name] = prompt_version(name)
        except FileNotFoundError:
            continue
    return versions


def write_results(path: Path, cases: dict[str, Any], model: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    from app.config import settings
    from app.rules import load_rule_config

    payload = {
        "model": model,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "analysis_version": settings.ANALYSIS_VERSION,
        "rules_version": load_rule_config(settings.DEFAULT_BANK_ID).version,
        "prompt_versions": prompt_versions(),
        "cases": cases,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run_one(client, case, directory: Path) -> dict[str, Any]:
    files = case.build(directory)
    created = client.post(
        "/api/cases", json={"bank_id": case.bank_id, "applicant_name": "Ravi Kumar"}
    )
    created.raise_for_status()
    case_id = created.json()["id"]

    payload = [
        ("files", (p.name, p.read_bytes(), "application/octet-stream"))
        for p in files.values()
    ]
    upload = client.post(f"/api/cases/{case_id}/documents", files=payload)
    upload.raise_for_status()

    client.post(f"/api/cases/{case_id}/analyze").raise_for_status()
    analysis = client.get(f"/api/cases/{case_id}/analysis")
    analysis.raise_for_status()
    return analysis.json()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the evaluation cases.")
    parser.add_argument("--live", action="store_true", help="use the configured endpoint")
    parser.add_argument("--cases", nargs="*", help="case ids to run (default: all)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--resume", action="store_true", help="skip cases already recorded")
    args = parser.parse_args()

    configure(args.live)

    import tempfile

    from fastapi.testclient import TestClient

    from app.config import settings
    from app.db import init_db
    from app.main import app
    from tests.evaluation.cases import CASES
    from tests.evaluation.runner import aggregate, format_report, score_case
    from tests.evaluation.runner import CaseScore

    init_db()
    selected = [c for c in CASES if not args.cases or c.case_id in args.cases]
    recorded = load_existing(args.out) if args.resume else {}
    model = settings.LLM_MODEL if args.live else "stub"

    print(f"model: {model}")
    print(f"cases: {', '.join(c.case_id for c in selected)}")
    if recorded:
        print(f"resuming, {len(recorded)} already recorded")
    print()

    with TestClient(app) as client:
        for case in selected:
            if case.case_id in recorded:
                print(f"  case {case.case_id}: skipped (already recorded)")
                continue

            directory = Path(tempfile.mkdtemp(prefix=f"eval{case.case_id}-"))
            started = time.perf_counter()
            try:
                analysis = run_one(client, case, directory)
            except Exception as exc:  # noqa: BLE001 - one bad case must not lose the rest
                print(f"  case {case.case_id}: ERROR {type(exc).__name__}: {exc}")
                recorded[case.case_id] = {"error": f"{type(exc).__name__}: {exc}"}
                write_results(args.out, recorded, model)
                continue

            score = score_case(case, analysis)
            elapsed = time.perf_counter() - started
            recorded[case.case_id] = {
                "description": case.description,
                "passed": score.passed,
                "expected_status": score.expected_status,
                "actual_status": score.actual_status,
                "true_positives": score.true_positives,
                "false_positives": score.false_positives,
                "false_negatives": score.false_negatives,
                "severity_correct": score.severity_correct,
                "severity_total": score.severity_total,
                "evidence_correct": score.evidence_correct,
                "evidence_total": score.evidence_total,
                "field_correct": score.field_correct,
                "field_total": score.field_total,
                "forbidden_raised": score.forbidden_raised,
                "notes": score.notes,
                "seconds": round(elapsed, 1),
                "findings": [
                    {"code": d["code"], "type": d["type"], "severity": d["severity"]}
                    for d in analysis.get("discrepancies", [])
                ],
            }
            # Written after every case, so an interrupted run keeps its work.
            write_results(args.out, recorded, model)

            mark = "PASS" if score.passed else "FAIL"
            print(
                f"  case {case.case_id}: {mark} in {elapsed:5.1f}s  "
                f"status={score.actual_status} tp={score.true_positives} "
                f"fp={score.false_positives} fn={score.false_negatives}"
            )
            for note in score.notes:
                print(f"        - {note}")

    scores = [
        CaseScore(
            case_id=cid,
            description=data.get("description", ""),
            status_correct=data.get("actual_status") in str(data.get("expected_status", "")),
            expected_status=str(data.get("expected_status", "")),
            actual_status=str(data.get("actual_status", "")),
            true_positives=data.get("true_positives", 0),
            false_positives=data.get("false_positives", 0),
            false_negatives=data.get("false_negatives", 0),
            severity_correct=data.get("severity_correct", 0),
            severity_total=data.get("severity_total", 0),
            evidence_correct=data.get("evidence_correct", 0),
            evidence_total=data.get("evidence_total", 0),
            field_correct=data.get("field_correct", 0),
            field_total=data.get("field_total", 0),
            forbidden_raised=data.get("forbidden_raised", []),
            notes=data.get("notes", []),
        )
        for cid, data in sorted(recorded.items())
        if "error" not in data
    ]

    if scores:
        metrics = aggregate(scores)
        print(format_report(scores, metrics))
        print(f"\nresults written to {args.out}")

    failures = [cid for cid, d in recorded.items() if "error" in d or not d.get("passed")]
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
