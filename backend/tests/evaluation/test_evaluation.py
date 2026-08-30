"""The evaluation suite.

Runs every case end to end and asserts the metrics that decide whether this
system is usable: precision above recall, no fabricated findings, correct
severity, and evidence that points where it claims to.

Against the mock client the extraction is a regular expression, so these
numbers measure the deterministic pipeline. Point LLM_BASE_URL at a real
endpoint and unset LLM_USE_MOCK to measure the model.
"""

from __future__ import annotations

import json

import pytest

from tests.evaluation.cases import CASES
from tests.evaluation.runner import aggregate, format_report, score_case

# Thresholds are stated as the system's contract. Precision is higher than
# recall on purpose: this tool exists to give reviewers a short, trustworthy
# list, not an exhaustive one.
MIN_PRECISION = 0.90
MIN_RECALL = 0.85
MIN_FIELD_ACCURACY = 0.95
MIN_SEVERITY_ACCURACY = 0.95
MIN_EVIDENCE_ACCURACY = 1.00
MAX_FALSE_POSITIVE_RATE = 0.10


def run_case(client, case, directory):
    files = case.build(directory)
    created = client.post(
        "/api/cases", json={"bank_id": case.bank_id, "applicant_name": "Ravi Kumar"}
    )
    assert created.status_code == 201, created.text
    case_id = created.json()["id"]

    payload = [
        ("files", (path.name, path.read_bytes(), "application/octet-stream"))
        for path in files.values()
    ]
    uploaded = client.post(f"/api/cases/{case_id}/documents", files=payload)
    assert uploaded.status_code == 200, uploaded.text

    analysed = client.post(f"/api/cases/{case_id}/analyze")
    assert analysed.status_code == 200, analysed.text

    response = client.get(f"/api/cases/{case_id}/analysis")
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture(scope="module")
def results(request):
    """Run every case once and share the scores across the assertions."""
    from fastapi.testclient import TestClient

    from app.main import app

    scores = []
    tmp_factory = request.config._tmp_path_factory  # noqa: SLF001
    with TestClient(app) as client:
        for case in CASES:
            directory = tmp_factory.mktemp(f"eval{case.case_id}")
            analysis = run_case(client, case, directory)
            scores.append(score_case(case, analysis))

    metrics = aggregate(scores)
    print(format_report(scores, metrics))
    return scores, metrics


class TestEvaluationMetrics:
    def test_field_extraction_accuracy(self, results):
        _, metrics = results
        assert metrics.field_accuracy >= MIN_FIELD_ACCURACY

    def test_discrepancy_precision(self, results):
        _, metrics = results
        assert metrics.precision >= MIN_PRECISION

    def test_discrepancy_recall(self, results):
        _, metrics = results
        assert metrics.recall >= MIN_RECALL

    def test_false_positive_rate(self, results):
        _, metrics = results
        assert metrics.false_positive_rate <= MAX_FALSE_POSITIVE_RATE

    def test_severity_accuracy(self, results):
        _, metrics = results
        assert metrics.severity_accuracy >= MIN_SEVERITY_ACCURACY

    def test_evidence_accuracy(self, results):
        """A finding that cites the wrong value is not a correct finding."""
        _, metrics = results
        assert metrics.evidence_accuracy >= MIN_EVIDENCE_ACCURACY

    def test_no_forbidden_findings_were_raised(self, results):
        scores, _ = results
        offenders = {
            score.case_id: score.forbidden_raised for score in scores if score.forbidden_raised
        }
        assert not offenders, f"harmless variation was flagged: {offenders}"

    def test_status_accuracy(self, results):
        _, metrics = results
        assert metrics.status_accuracy >= 0.85


class TestIndividualCases:
    def test_every_case_reaches_its_expected_status(self, results):
        scores, _ = results
        wrong = {
            score.case_id: (score.expected_status, score.actual_status)
            for score in scores
            if not score.status_correct
        }
        assert not wrong, f"status mismatches: {wrong}"

    def test_no_expected_finding_was_missed(self, results):
        scores, _ = results
        missed = {score.case_id: score.notes for score in scores if score.false_negatives}
        assert not missed, f"missed findings: {missed}"

    def test_metrics_are_reportable(self, results, tmp_path):
        """The suite writes its numbers out, so a run can be compared later."""
        _, metrics = results
        target = tmp_path / "evaluation.json"
        target.write_text(json.dumps(metrics.as_dict(), indent=2), encoding="utf-8")
        assert json.loads(target.read_text())["discrepancy_precision"] >= MIN_PRECISION
