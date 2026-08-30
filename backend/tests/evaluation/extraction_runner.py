"""Scoring one degraded document, and reporting the sweep.

Three outcomes are counted per field, not two, and the distinction is the whole
point of this module:

- **correct** — the value was read.
- **missing** — no value was returned. Recoverable: the case goes to a human
  with a gap in it, which is what a queue is for.
- **wrong** — a value was returned and it is not the value on the page. This
  is the one that costs money, because nothing downstream can tell it from a
  correct read, and a wrong date of birth propagates into a discrepancy that
  either fires against a clean applicant or fails to fire against a dirty one.

So the headline number here is not accuracy. It is the rate at which the system
asserts something false *without* flagging the page — silent wrongness. A
pipeline that reads nothing off an unreadable scan and says so is behaving
correctly; one that reads three fields off it confidently is not, even if two
of them happen to be right.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tests.evaluation.extraction_cases import (
    SCORING_KINDS,
    SIMILARITY_FIELDS,
    SIMILARITY_THRESHOLD,
    Variant,
)

CORRECT = "correct"
WRONG = "wrong"
MISSING = "missing"

# The transcription prompt tells the model to write this where it cannot read
# the page, and the extractor passes it through. A value carrying it is a
# declared gap, not an assertion, so it counts as missing: penalising it as a
# wrong read would score the model down for being honest, and push the design
# towards guessing.
ILLEGIBLE = "[ILLEGIBLE]"


@dataclass
class FieldOutcome:
    name: str
    expected: str
    verdict: str
    got: str | None = None
    normalized: str | None = None
    confidence: float | None = None
    note: str = ""


@dataclass
class VariantScore:
    variant_id: str
    doc_id: str
    degradation: str
    condition: str
    # "read" (extraction should still succeed) or "flag" (the page is bad
    # enough that a quality flag is the correct answer).
    expectation: str
    classified_type: str = ""
    type_correct: bool = False
    is_readable: bool = True
    quality_status: str = ""
    quality_flags: list[str] = field(default_factory=list)
    ocr_confidence: float | None = None
    text_length: int = 0
    fields: list[FieldOutcome] = field(default_factory=list)
    seconds: float = 0.0
    error: str | None = None

    @property
    def counted(self) -> dict[str, int]:
        tally = {CORRECT: 0, WRONG: 0, MISSING: 0}
        for outcome in self.fields:
            tally[outcome.verdict] += 1
        return tally

    @property
    def accuracy(self) -> float:
        return self.counted[CORRECT] / len(self.fields) if self.fields else 0.0

    @property
    def flagged(self) -> bool:
        """Did the pipeline say, in any way it can, that this page is doubtful?"""
        return (
            not self.is_readable
            or self.quality_status in {"REVIEW_REQUIRED", "UNABLE_TO_VERIFY"}
            or bool({"UNCLEAR_IMAGE", "LOW_OCR_CONFIDENCE", "UNREADABLE"} & set(self.quality_flags))
        )

    @property
    def silently_wrong(self) -> int:
        """Wrong values asserted on a page that was never flagged."""
        return 0 if self.flagged else self.counted[WRONG]

    @property
    def honest(self) -> bool:
        """Behaved acceptably for what this condition demands.

        Under "flag", the page is meant to be refused or marked doubtful;
        reading it correctly anyway is also fine, since a correct read needs no
        warning. Under "read", every field is expected and nothing may be wrong.
        """
        if self.error:
            return False
        if self.expectation == "flag":
            return self.flagged or (self.counted[WRONG] == 0 and self.counted[MISSING] == 0)
        return self.counted[WRONG] == 0 and self.counted[MISSING] == 0


def compare(field_name: str, expected: str, got: str | None) -> str:
    """One field: correct, wrong or missing.

    Both sides are normalised here rather than reading the ``normalized_value``
    the pipeline stored on the row. It is the same production function either
    way, so a normalisation bug is still caught — but re-deriving means a fix
    to the normaliser can be re-scored against a recorded run instead of
    costing another sweep. The stored form is kept on the outcome for the
    report to quote.
    """
    from app.utils.normalize import normalize_value
    from app.utils.normalize import kind_for_field
    from app.utils.text import token_set_similarity

    if got is None or ILLEGIBLE in str(got).upper():
        return MISSING

    # An off-profile field normalises as free text, where "42,000.00" and
    # "42000" differ. SCORING_KINDS states the kind where that is the wrong
    # question.
    kind = SCORING_KINDS.get(field_name) or kind_for_field(field_name)
    wanted = normalize_value(kind, expected).normalized
    actual = normalize_value(kind, got).normalized

    if wanted and actual and wanted == actual:
        return CORRECT
    if field_name in SIMILARITY_FIELDS:
        if token_set_similarity(str(expected), str(got)) >= SIMILARITY_THRESHOLD:
            return CORRECT
    return WRONG


def score_variant(variant: Variant, observed: dict[str, Any]) -> VariantScore:
    """Compare what the pipeline produced for one document against its truth.

    ``observed`` is what the runner read back off the database: the document
    row, the page, and the field values. Keeping it a plain dict means the
    scoring can be exercised without a database.
    """
    document = variant.document
    score = VariantScore(
        variant_id=variant.variant_id,
        doc_id=variant.doc_id,
        degradation=variant.degradation,
        condition=variant.condition.description,
        expectation=variant.condition.expectation,
        classified_type=observed.get("document_type", ""),
        is_readable=bool(observed.get("is_readable", True)),
        quality_status=observed.get("quality_status", ""),
        quality_flags=list(observed.get("quality_flags", [])),
        ocr_confidence=observed.get("ocr_confidence"),
        text_length=int(observed.get("text_length", 0)),
        seconds=float(observed.get("seconds", 0.0)),
        error=observed.get("error"),
    )
    score.type_correct = score.classified_type in document.expected_type

    values: dict[str, list[dict[str, Any]]] = observed.get("fields", {})
    for name, expected in document.truth.items():
        rows = values.get(name) or []
        if not rows:
            score.fields.append(FieldOutcome(name=name, expected=expected, verdict=MISSING))
            continue

        # Several documents can assert the same canonical field; any row that
        # matches counts, and the best row is the one reported.
        best: FieldOutcome | None = None
        for row in rows:
            verdict = compare(name, expected, row.get("original"))
            outcome = FieldOutcome(
                name=name,
                expected=expected,
                verdict=verdict,
                got=row.get("original"),
                normalized=row.get("normalized"),
                confidence=row.get("confidence"),
            )
            if verdict == CORRECT:
                best = outcome
                break
            if best is None:
                best = outcome
        assert best is not None
        if best.verdict == MISSING and best.got:
            best.note = "declared illegible"
        if best.verdict == WRONG and best.got:
            decoy = document.decoys.get(name)
            if decoy and str(best.got).strip() in decoy:
                best.note = "picked up the decoy"
        score.fields.append(best)

    return score


# --------------------------------------------------------------------------
# Aggregation and reporting
# --------------------------------------------------------------------------
@dataclass
class Totals:
    correct: int = 0
    wrong: int = 0
    missing: int = 0
    silently_wrong: int = 0
    variants: int = 0
    honest: int = 0
    type_correct: int = 0
    flagged: int = 0
    errors: int = 0

    @property
    def fields(self) -> int:
        return self.correct + self.wrong + self.missing

    @property
    def accuracy(self) -> float:
        return self.correct / self.fields if self.fields else 0.0

    @property
    def wrong_rate(self) -> float:
        return self.wrong / self.fields if self.fields else 0.0

    @property
    def silent_rate(self) -> float:
        return self.silently_wrong / self.fields if self.fields else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "variants": self.variants,
            "fields": self.fields,
            "field_accuracy": round(self.accuracy, 3),
            "wrong_rate": round(self.wrong_rate, 3),
            "silently_wrong_rate": round(self.silent_rate, 3),
            "missing": self.missing,
            "classification_accuracy": round(
                self.type_correct / self.variants if self.variants else 0.0, 3
            ),
            "honest_variants": self.honest,
            "flagged_variants": self.flagged,
            "errors": self.errors,
        }


def add(totals: Totals, score: VariantScore) -> Totals:
    counted = score.counted
    totals.correct += counted[CORRECT]
    totals.wrong += counted[WRONG]
    totals.missing += counted[MISSING]
    totals.silently_wrong += score.silently_wrong
    totals.variants += 1
    totals.honest += int(score.honest)
    totals.type_correct += int(score.type_correct)
    totals.flagged += int(score.flagged)
    totals.errors += int(bool(score.error))
    return totals


def aggregate(scores: list[VariantScore]) -> Totals:
    totals = Totals()
    for score in scores:
        add(totals, score)
    return totals


def by_degradation(scores: list[VariantScore]) -> dict[str, Totals]:
    grouped: dict[str, Totals] = {}
    for score in scores:
        add(grouped.setdefault(score.degradation, Totals()), score)
    return grouped


def by_document(scores: list[VariantScore]) -> dict[str, Totals]:
    grouped: dict[str, Totals] = {}
    for score in scores:
        add(grouped.setdefault(score.doc_id, Totals()), score)
    return grouped


def format_report(scores: list[VariantScore]) -> str:
    """A report meant to be read for where it breaks, not for its average."""
    lines = ["", "Extraction under degradation", "=" * 88]

    lines.append("")
    lines.append("By condition (sorted worst first)")
    lines.append(
        f"  {'condition':<16} {'acc':>6} {'wrong':>6} {'silent':>7} {'miss':>5} "
        f"{'flagged':>8} {'type':>5}"
    )
    grouped = by_degradation(scores)
    for name, totals in sorted(grouped.items(), key=lambda item: item[1].accuracy):
        lines.append(
            f"  {name:<16} {totals.accuracy:>6.2f} {totals.wrong:>6} "
            f"{totals.silently_wrong:>7} {totals.missing:>5} "
            f"{totals.flagged}/{totals.variants:<6} {totals.type_correct}/{totals.variants}"
        )

    lines.append("")
    lines.append("By document")
    for name, totals in sorted(by_document(scores).items(), key=lambda item: item[1].accuracy):
        lines.append(
            f"  {name:<16} {totals.accuracy:>6.2f} accuracy, {totals.wrong} wrong, "
            f"{totals.missing} missing"
        )

    problems = [
        score for score in scores
        if not score.honest or score.silently_wrong or score.error
    ]
    if problems:
        lines.append("")
        lines.append("Where it broke")
        for score in problems:
            head = f"  {score.variant_id:<28} {score.condition}"
            if score.error:
                lines.append(f"{head}\n        ERROR {score.error}")
                continue
            lines.append(head)
            lines.append(
                f"        classified {score.classified_type or '-'} "
                f"({'ok' if score.type_correct else 'WRONG TYPE'}), "
                f"quality {score.quality_status or '-'}, "
                f"flags {','.join(score.quality_flags) or '-'}"
            )
            for outcome in score.fields:
                if outcome.verdict == CORRECT:
                    continue
                detail = (
                    f"got {outcome.got!r}"
                    if outcome.verdict == WRONG or outcome.got
                    else "not extracted"
                )
                note = f" [{outcome.note}]" if outcome.note else ""
                lines.append(
                    f"        {outcome.verdict:<8} {outcome.name}: "
                    f"expected {outcome.expected!r}, {detail}{note}"
                )

    totals = aggregate(scores)
    lines.append("")
    lines.append("-" * 88)
    for key, value in totals.as_dict().items():
        lines.append(f"  {key}: {value}")
    lines.append("")
    lines.append(
        "  silently_wrong_rate is the number that matters: a false value on a page "
        "nothing flagged."
    )
    return "\n".join(lines)
