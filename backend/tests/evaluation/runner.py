"""Scoring an evaluation run.

The metrics that matter for this system are asymmetric. A missed discrepancy is
bad; a fabricated one is worse, because it trains reviewers to disregard the
queue. So precision is reported separately from recall and the false-positive
rate is a headline number rather than a footnote.

Evidence accuracy is scored too: a finding that is correct but cites the wrong
page is not usable, and a system that scored it as a hit would be lying to
itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tests.evaluation.cases import EvaluationCase


@dataclass
class CaseScore:
    case_id: str
    description: str
    status_correct: bool
    expected_status: str
    actual_status: str
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    severity_correct: int = 0
    severity_total: int = 0
    evidence_correct: int = 0
    evidence_total: int = 0
    field_correct: int = 0
    field_total: int = 0
    forbidden_raised: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return (
            self.status_correct
            and not self.forbidden_raised
            and self.false_negatives == 0
            and self.field_correct == self.field_total
        )


@dataclass
class Metrics:
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    severity_correct: int = 0
    severity_total: int = 0
    evidence_correct: int = 0
    evidence_total: int = 0
    field_correct: int = 0
    field_total: int = 0
    status_correct: int = 0
    status_total: int = 0
    forbidden_raised: int = 0

    @property
    def precision(self) -> float:
        denominator = self.true_positives + self.false_positives
        return self.true_positives / denominator if denominator else 1.0

    @property
    def recall(self) -> float:
        denominator = self.true_positives + self.false_negatives
        return self.true_positives / denominator if denominator else 1.0

    @property
    def f1(self) -> float:
        if self.precision + self.recall == 0:
            return 0.0
        return 2 * self.precision * self.recall / (self.precision + self.recall)

    @property
    def false_positive_rate(self) -> float:
        total = self.true_positives + self.false_positives
        return self.false_positives / total if total else 0.0

    @property
    def severity_accuracy(self) -> float:
        return self.severity_correct / self.severity_total if self.severity_total else 1.0

    @property
    def evidence_accuracy(self) -> float:
        return self.evidence_correct / self.evidence_total if self.evidence_total else 1.0

    @property
    def field_accuracy(self) -> float:
        return self.field_correct / self.field_total if self.field_total else 1.0

    @property
    def status_accuracy(self) -> float:
        return self.status_correct / self.status_total if self.status_total else 1.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "field_accuracy": round(self.field_accuracy, 3),
            "discrepancy_precision": round(self.precision, 3),
            "discrepancy_recall": round(self.recall, 3),
            "f1": round(self.f1, 3),
            "false_positive_rate": round(self.false_positive_rate, 3),
            "severity_accuracy": round(self.severity_accuracy, 3),
            "evidence_accuracy": round(self.evidence_accuracy, 3),
            "status_accuracy": round(self.status_accuracy, 3),
            "forbidden_findings_raised": self.forbidden_raised,
            "counts": {
                "true_positives": self.true_positives,
                "false_positives": self.false_positives,
                "false_negatives": self.false_negatives,
            },
        }


def score_case(case: EvaluationCase, analysis: dict[str, Any]) -> CaseScore:
    """Compare one analysis against what the case expects."""
    findings = analysis.get("discrepancies", [])
    by_type: dict[str, list[dict]] = {}
    for finding in findings:
        by_type.setdefault(finding["type"], []).append(finding)

    score = CaseScore(
        case_id=case.case_id,
        description=case.description,
        status_correct=str(analysis.get("final_status")) in case.acceptable_statuses,
        expected_status=" or ".join(case.acceptable_statuses),
        actual_status=str(analysis.get("final_status")),
    )

    expected_types = {expected.type for expected in case.expected_findings}

    for expected in case.expected_findings:
        actual = by_type.get(expected.type)
        if not actual:
            score.false_negatives += 1
            score.notes.append(f"missed {expected.type}")
            continue

        score.true_positives += 1
        finding = actual[0]

        score.severity_total += 1
        if finding["severity"] == expected.severity:
            score.severity_correct += 1
        else:
            score.notes.append(
                f"{expected.type} severity {finding['severity']}, expected {expected.severity}"
            )

        if expected.values:
            score.evidence_total += 1
            cited = {reference["value"] for reference in finding.get("evidence", [])}
            if set(expected.values) <= cited:
                score.evidence_correct += 1
            else:
                score.notes.append(
                    f"{expected.type} evidence cites {sorted(cited)}, expected {list(expected.values)}"
                )

    # Anything raised that the case did not expect, excluding low-severity
    # observations which are advisory rather than assertions about the applicant.
    for finding in findings:
        if finding["type"] in expected_types:
            continue
        if finding["type"] in case.forbidden_findings:
            score.forbidden_raised.append(finding["type"])
            score.false_positives += 1
        elif finding["severity"] in {"HIGH", "MEDIUM"}:
            score.false_positives += 1
            score.notes.append(f"unexpected {finding['severity']} finding: {finding['type']}")

    for name, expected_value in case.expected_fields.items():
        score.field_total += 1
        actual_field = analysis.get("applicant", {}).get("fields", {}).get(name, {})
        if actual_field.get("value") == expected_value:
            score.field_correct += 1
        else:
            score.notes.append(
                f"field {name} was {actual_field.get('value')!r}, expected {expected_value!r}"
            )

    missing = {item["document_type"] for item in analysis.get("missing_documents", [])}
    for required in case.expected_missing:
        if required not in missing:
            score.false_negatives += 1
            score.notes.append(f"did not report {required} as missing")

    return score


def aggregate(scores: list[CaseScore]) -> Metrics:
    metrics = Metrics()
    for score in scores:
        metrics.true_positives += score.true_positives
        metrics.false_positives += score.false_positives
        metrics.false_negatives += score.false_negatives
        metrics.severity_correct += score.severity_correct
        metrics.severity_total += score.severity_total
        metrics.evidence_correct += score.evidence_correct
        metrics.evidence_total += score.evidence_total
        metrics.field_correct += score.field_correct
        metrics.field_total += score.field_total
        metrics.status_total += 1
        metrics.status_correct += int(score.status_correct)
        metrics.forbidden_raised += len(score.forbidden_raised)
    return metrics


def format_report(scores: list[CaseScore], metrics: Metrics) -> str:
    lines = ["", "Evaluation results", "=" * 78]
    for score in scores:
        mark = "PASS" if score.passed else "FAIL"
        lines.append(f"[{mark}] case {score.case_id}: {score.description}")
        lines.append(
            f"        status {score.actual_status} (expected {score.expected_status}); "
            f"tp={score.true_positives} fp={score.false_positives} fn={score.false_negatives}"
        )
        for note in score.notes:
            lines.append(f"        - {note}")

    lines.append("-" * 78)
    for key, value in metrics.as_dict().items():
        lines.append(f"  {key}: {value}")
    return "\n".join(lines)
