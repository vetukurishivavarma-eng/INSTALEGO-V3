"""Land title rules: is the property what the applicant says, and whose was it.

Identity rules ask whether every document describes the same person. These ask
a different question about a different subject — the land — and the two must
not be confused. A previous owner appearing on a twenty-year-old deed is not a
discrepancy about the applicant; it is the point of the document. That is why
``seller_name``, ``buyer_name`` and ``property_owner_name`` are canonical fields
of their own rather than folded into ``name``: pushing them onto the applicant
profile would manufacture a name conflict on every honest chain of title.

The chain is the substance here. Deeds are ordered by registration date and
each transfer must begin where the previous one ended: the seller in a deed has
to be the party who bought it last time. A break in that sequence is the
classic signature of a defective title, and it is deterministic to check, so no
model is asked about it.

Everything here runs only on the documents actually supplied. Absence is
handled as report completeness, not as a finding — see
``optional_document_sets`` in the configuration and ``_completeness`` in the
analysis workflow.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.comparison import ComparisonVerdict, FuzzyThresholds, compare_field
from app.models.enums import DocumentType, RuleResult, Severity
from app.rules.registry import FieldObservation, RuleContext, RuleOutcome, rule
from app.schemas.discrepancy import CandidateDiscrepancy
from app.utils.dates import parse_date

# Every type that says something about the land, as opposed to the applicant.
LAND_DOCUMENT_TYPES: tuple[str, ...] = (
    DocumentType.SALE_DEED,
    DocumentType.ENCUMBRANCE_CERTIFICATE,
    DocumentType.KHATA_CERTIFICATE,
    DocumentType.PROPERTY_TAX_RECEIPT,
    DocumentType.PROPERTY_DOCUMENT,
)

# Values an encumbrance certificate uses to say "nothing is registered against
# this property". Anything else is treated as a subsisting charge, because the
# safe default when the wording is unfamiliar is to surface it for a human.
NIL_ENCUMBRANCE = {"NIL", "NONE", "NO ENCUMBRANCE", "NO ENCUMBRANCES", "CLEAR", "NAF"}


def _thresholds(context: RuleContext) -> FuzzyThresholds:
    configured = context.config.thresholds
    return FuzzyThresholds(
        name_equal=float(configured.get("name_equal", 0.92)),
        name_different=float(configured.get("name_different", 0.70)),
        address_equal=float(configured.get("address_equal", 0.90)),
        address_different=float(configured.get("address_different", 0.55)),
    )


def _not_applicable(rule_id: str, reason: str) -> RuleOutcome:
    return RuleOutcome(
        rule_id=rule_id, category="land", result=RuleResult.NOT_APPLICABLE, reason=reason
    )


# --------------------------------------------------------------------------
# The chain of title
# --------------------------------------------------------------------------
@dataclass
class Transfer:
    """One registered transfer, as read off a single deed."""

    document_id: str
    document_name: str
    seller: FieldObservation | None
    buyer: FieldObservation | None
    registered_on: str | None
    sort_key: str

    @property
    def describes_a_transfer(self) -> bool:
        return bool(self.seller and self.buyer)


def _transfers(context: RuleContext) -> list[Transfer]:
    """Every deed, ordered by the date it was registered.

    Only SALE_DEED is considered. A khata extract or a tax receipt names an
    owner but records no transfer, and letting one contribute a link would
    invent a conveyance that never happened.
    """
    transfers: list[Transfer] = []
    for document in context.documents_of_type(DocumentType.SALE_DEED):
        registered = context.observation_for_document(document.document_id, "registration_date")
        parsed = parse_date(registered.value) if registered and registered.value else None
        transfers.append(
            Transfer(
                document_id=document.document_id,
                document_name=document.filename,
                seller=context.observation_for_document(document.document_id, "seller_name"),
                buyer=context.observation_for_document(document.document_id, "buyer_name"),
                registered_on=parsed.iso if parsed and parsed.ok else None,
                # Undated deeds sort last, where they are reported rather than
                # silently slotted into an order they do not support.
                sort_key=(parsed.iso if parsed and parsed.ok else "9999-99-99"),
            )
        )
    return sorted(transfers, key=lambda t: t.sort_key)


@rule("land", "ownership_chain")
def ownership_chain(context: RuleContext) -> Iterable[RuleOutcome]:
    """Each deed must begin where the previous one ended."""
    rule_id = "land.ownership_chain"
    severity = context.config.severity("land", "ownership_chain", Severity.HIGH)
    transfers = _transfers(context)

    if not transfers:
        yield _not_applicable(rule_id, "no sale deed was supplied")
        return

    usable = [t for t in transfers if t.describes_a_transfer]
    if len(usable) < 2:
        yield RuleOutcome(
            rule_id=rule_id,
            category="land",
            result=RuleResult.NOT_APPLICABLE,
            field="ownership_chain",
            reason=(
                "a chain needs at least two deeds; "
                f"{len(usable)} deed(s) named both a seller and a buyer"
            ),
            evidence=[o.as_evidence() for t in usable for o in (t.seller, t.buyer) if o],
        )
        return

    thresholds = _thresholds(context)
    breaks = 0

    for previous, current in zip(usable, usable[1:], strict=False):
        assert previous.buyer and current.seller
        outcome = compare_field(
            "name", previous.buyer.value, current.seller.value, thresholds=thresholds
        )
        evidence = [previous.buyer.as_evidence(), current.seller.as_evidence()]

        if outcome.verdict == ComparisonVerdict.EQUAL:
            continue

        breaks += 1
        # Only a plainly different party is a break. A comparison that declined
        # to decide -- an unreadable name, an initial against a full name --
        # goes for a second opinion rather than being called a defective title.
        undecided = outcome.verdict != ComparisonVerdict.DIFFERENT
        summary = (
            f"{previous.document_name} transferred the property to "
            f"{previous.buyer.value}, but {current.document_name} is sold by "
            f"{current.seller.value}. A transfer from "
            f"{previous.buyer.value} to {current.seller.value} is not accounted for."
        )
        yield RuleOutcome(
            rule_id=rule_id,
            category="land",
            result=RuleResult.REVIEW if undecided else RuleResult.FAIL,
            field="ownership_chain",
            severity=severity,
            reason=summary,
            evidence=evidence,
            candidate=CandidateDiscrepancy(
                type="OWNERSHIP_CHAIN_BREAK",
                field="ownership_chain",
                severity=severity,
                rule_id=rule_id,
                origin="RULE_ENGINE",
                comparison_method="name",
                summary=summary,
                values=[previous.buyer.value, current.seller.value],
                evidence=evidence,
                # A name that a fuzzy comparison could not settle is worth a
                # second opinion; a plainly different party is not.
                needs_reasoning=undecided,
                deterministic=not undecided,
            ),
        )

    undated = [t for t in usable if not t.registered_on]
    if undated:
        names = ", ".join(t.document_name for t in undated)
        yield RuleOutcome(
            rule_id=rule_id,
            category="land",
            result=RuleResult.REVIEW,
            field="ownership_chain",
            severity=Severity.MEDIUM,
            reason=(
                f"no registration date was read from {names}, so the order of "
                "the chain could not be established from the documents alone"
            ),
            evidence=[o.as_evidence() for t in undated for o in (t.seller, t.buyer) if o],
        )

    if not breaks:
        yield RuleOutcome(
            rule_id=rule_id,
            category="land",
            result=RuleResult.PASS,
            field="ownership_chain",
            reason=(
                f"the chain of title runs unbroken across {len(usable)} deeds, "
                f"from {usable[0].seller.value} to {usable[-1].buyer.value}"
            ),
            evidence=[o.as_evidence() for t in usable for o in (t.seller, t.buyer) if o],
        )


@rule("land", "chain_dates_ordered")
def chain_dates_ordered(context: RuleContext) -> Iterable[RuleOutcome]:
    """A property cannot be sold by someone before they bought it."""
    rule_id = "land.chain_dates_ordered"
    severity = context.config.severity("land", "chain_dates_ordered", Severity.MEDIUM)
    dated = [t for t in _transfers(context) if t.registered_on]

    if len(dated) < 2:
        yield _not_applicable(rule_id, "fewer than two dated deeds were supplied")
        return

    duplicates = [
        (a, b) for a, b in zip(dated, dated[1:], strict=False) if a.registered_on == b.registered_on
    ]
    for earlier, later in duplicates:
        summary = (
            f"{earlier.document_name} and {later.document_name} are both registered on "
            f"{earlier.registered_on}, so the order of the transfers cannot be "
            "established from the deeds."
        )
        yield RuleOutcome(
            rule_id=rule_id,
            category="land",
            result=RuleResult.REVIEW,
            field="ownership_chain",
            severity=severity,
            reason=summary,
            candidate=CandidateDiscrepancy(
                type="CHAIN_DATES_AMBIGUOUS",
                field="ownership_chain",
                severity=severity,
                rule_id=rule_id,
                origin="RULE_ENGINE",
                comparison_method="date",
                summary=summary,
                values=[str(earlier.registered_on), str(later.registered_on)],
                evidence=[],
                needs_reasoning=False,
                deterministic=True,
            ),
        )

    if not duplicates:
        yield RuleOutcome(
            rule_id=rule_id,
            category="land",
            result=RuleResult.PASS,
            field="ownership_chain",
            reason=f"the {len(dated)} dated deeds run forward in time",
        )


# --------------------------------------------------------------------------
# Is every document about the same piece of land
# --------------------------------------------------------------------------
@rule("land", "property_identity")
def property_identity(context: RuleContext) -> Iterable[RuleOutcome]:
    """The survey number is what identifies the land itself.

    Addresses are written a dozen ways and are compared leniently elsewhere;
    a survey number is an identifier and either matches or does not. Two land
    documents describing different survey numbers are describing different
    land, which matters more than any disagreement inside one of them.
    """
    rule_id = "land.property_identity"
    severity = context.config.severity("land", "property_identity", Severity.HIGH)
    observations = [
        o for o in context.values_for("survey_number")
        if o.normalized_value and o.document_type in LAND_DOCUMENT_TYPES
    ]

    if len(observations) < 2:
        yield _not_applicable(
            rule_id, "fewer than two land documents stated a survey number"
        )
        return

    reference = observations[0]
    mismatches = [o for o in observations[1:] if o.normalized_value != reference.normalized_value]

    for other in mismatches:
        summary = (
            f"{reference.document_name} concerns survey number {reference.value}; "
            f"{other.document_name} concerns {other.value}. These are different "
            "parcels of land."
        )
        evidence = [reference.as_evidence(), other.as_evidence()]
        yield RuleOutcome(
            rule_id=rule_id,
            category="land",
            result=RuleResult.FAIL,
            field="survey_number",
            severity=severity,
            reason=summary,
            evidence=evidence,
            candidate=CandidateDiscrepancy(
                type="PROPERTY_IDENTITY_MISMATCH",
                field="survey_number",
                severity=severity,
                rule_id=rule_id,
                origin="RULE_ENGINE",
                comparison_method="exact",
                summary=summary,
                values=[reference.value, other.value],
                evidence=evidence,
                needs_reasoning=False,
                deterministic=True,
            ),
        )

    if not mismatches:
        yield RuleOutcome(
            rule_id=rule_id,
            category="land",
            result=RuleResult.PASS,
            field="survey_number",
            reason=(
                f"all {len(observations)} land documents concern survey number "
                f"{reference.value}"
            ),
            evidence=[o.as_evidence() for o in observations],
        )


# --------------------------------------------------------------------------
# Does the land belong to the applicant
# --------------------------------------------------------------------------
@rule("land", "current_owner_is_applicant")
def current_owner_is_applicant(context: RuleContext) -> Iterable[RuleOutcome]:
    """The person taking the loan should be the person who owns the land.

    Reported as a land finding rather than a name mismatch, and worded as what
    it usually is: a mutation that has not been carried through, rather than a
    contradiction about who the applicant is.
    """
    rule_id = "land.current_owner_is_applicant"
    severity = context.config.severity("land", "current_owner_is_applicant", Severity.HIGH)

    applicant = context.profile.fields.get("name")
    applicant_name = getattr(applicant, "value", None)
    if not applicant_name:
        yield _not_applicable(rule_id, "the applicant's name has not been established")
        return

    claims: list[FieldObservation] = [
        o for o in context.values_for("property_owner_name")
        if o.value and o.document_type in LAND_DOCUMENT_TYPES
    ]
    transfers = [t for t in _transfers(context) if t.buyer]
    if transfers:
        claims.append(transfers[-1].buyer)

    if not claims:
        yield _not_applicable(rule_id, "no land document named an owner")
        return

    thresholds = _thresholds(context)
    disagreeing = []
    for claim in claims:
        outcome = compare_field("name", applicant_name, claim.value, thresholds=thresholds)
        if outcome.verdict != ComparisonVerdict.EQUAL:
            disagreeing.append((claim, outcome))

    for claim, outcome in disagreeing:
        undecided = outcome.verdict != ComparisonVerdict.DIFFERENT
        summary = (
            f"{claim.document_name} records the property as belonging to "
            f"{claim.value}, while the applicant is {applicant_name}. The "
            "property is not recorded in the applicant's name on this document."
        )
        evidence = [claim.as_evidence()]
        yield RuleOutcome(
            rule_id=rule_id,
            category="land",
            result=RuleResult.REVIEW if undecided else RuleResult.FAIL,
            field="property_owner_name",
            severity=severity,
            reason=summary,
            evidence=evidence,
            candidate=CandidateDiscrepancy(
                type="PROPERTY_NOT_IN_APPLICANT_NAME",
                field="property_owner_name",
                severity=severity,
                rule_id=rule_id,
                origin="RULE_ENGINE",
                comparison_method="name",
                summary=summary,
                values=[applicant_name, claim.value],
                evidence=evidence,
                needs_reasoning=undecided,
                deterministic=not undecided,
            ),
        )

    if not disagreeing:
        yield RuleOutcome(
            rule_id=rule_id,
            category="land",
            result=RuleResult.PASS,
            field="property_owner_name",
            reason=f"every land document records the property in the name of {applicant_name}",
            evidence=[claim.as_evidence() for claim in claims],
        )


# --------------------------------------------------------------------------
# Encumbrance and tax
# --------------------------------------------------------------------------
@rule("land", "encumbrance")
def encumbrance(context: RuleContext) -> Iterable[RuleOutcome]:
    """A charge already registered against the land is the point of an EC."""
    rule_id = "land.encumbrance"
    severity = context.config.severity("land", "encumbrance", Severity.HIGH)
    statements = [
        o for o in context.values_for("encumbrance_status")
        if o.value and o.document_type == DocumentType.ENCUMBRANCE_CERTIFICATE
    ]

    if not statements:
        yield _not_applicable(rule_id, "no encumbrance certificate was supplied")
        return

    charged = [o for o in statements if (o.normalized_value or o.value).strip().upper()
               not in NIL_ENCUMBRANCE]

    for statement in charged:
        summary = (
            f"{statement.document_name} records an encumbrance on the property: "
            f"{statement.value}. A subsisting charge affects what the property "
            "can secure."
        )
        evidence = [statement.as_evidence()]
        yield RuleOutcome(
            rule_id=rule_id,
            category="land",
            result=RuleResult.FAIL,
            field="encumbrance_status",
            severity=severity,
            reason=summary,
            evidence=evidence,
            candidate=CandidateDiscrepancy(
                type="PROPERTY_ENCUMBERED",
                field="encumbrance_status",
                severity=severity,
                rule_id=rule_id,
                origin="RULE_ENGINE",
                comparison_method="exact",
                summary=summary,
                values=[statement.value],
                evidence=evidence,
                # What an unfamiliar wording amounts to is a judgement the
                # rule engine should not make on its own.
                needs_reasoning=True,
                deterministic=False,
            ),
        )

    if not charged:
        yield RuleOutcome(
            rule_id=rule_id,
            category="land",
            result=RuleResult.PASS,
            field="encumbrance_status",
            reason="the encumbrance certificate records no subsisting charge",
            evidence=[o.as_evidence() for o in statements],
        )


@rule("land", "encumbrance_period")
def encumbrance_period(context: RuleContext) -> Iterable[RuleOutcome]:
    """An EC covering three years does not answer a thirteen-year question."""
    rule_id = "land.encumbrance_period"
    settings = context.config.rule("land", "encumbrance_period")
    severity = context.config.severity("land", "encumbrance_period", Severity.MEDIUM)
    wanted = int(settings.get("required_years", 13))

    starts = [o for o in context.values_for("ec_period_from") if o.value]
    ends = [o for o in context.values_for("ec_period_to") if o.value]
    if not starts or not ends:
        yield _not_applicable(rule_id, "no encumbrance certificate period was read")
        return

    first, last = parse_date(starts[0].value), parse_date(ends[-1].value)
    if not (first.ok and last.ok):
        yield RuleOutcome(
            rule_id=rule_id,
            category="land",
            result=RuleResult.REVIEW,
            field="ec_period",
            severity=severity,
            reason="the certificate's period could not be read as dates",
            evidence=[starts[0].as_evidence(), ends[-1].as_evidence()],
        )
        return

    covered = (last.value - first.value).days / 365.25
    if covered + 0.5 >= wanted:
        yield RuleOutcome(
            rule_id=rule_id,
            category="land",
            result=RuleResult.PASS,
            field="ec_period",
            reason=f"the certificate covers {covered:.0f} years, against {wanted} required",
        )
        return

    summary = (
        f"the encumbrance certificate covers {covered:.0f} years "
        f"({starts[0].value} to {ends[-1].value}), where {wanted} are required. "
        "Anything registered before that period is not reported here."
    )
    yield RuleOutcome(
        rule_id=rule_id,
        category="land",
        result=RuleResult.FAIL,
        field="ec_period",
        severity=severity,
        reason=summary,
        evidence=[starts[0].as_evidence(), ends[-1].as_evidence()],
        candidate=CandidateDiscrepancy(
            type="ENCUMBRANCE_PERIOD_SHORT",
            field="ec_period",
            severity=severity,
            rule_id=rule_id,
            origin="RULE_ENGINE",
            comparison_method="date",
            summary=summary,
            values=[starts[0].value, ends[-1].value],
            evidence=[starts[0].as_evidence(), ends[-1].as_evidence()],
            needs_reasoning=False,
            deterministic=True,
        ),
    )


@rule("land", "tax_currency")
def tax_currency(context: RuleContext) -> Iterable[RuleOutcome]:
    """A stale receipt is not evidence that the dues are clear."""
    rule_id = "land.tax_currency"
    settings = context.config.rule("land", "tax_currency")
    severity = context.config.severity("land", "tax_currency", Severity.LOW)
    max_age = float(settings.get("max_age_years", 2))

    receipts = [o for o in context.values_for("receipt_date") if o.value]
    if not receipts:
        yield _not_applicable(rule_id, "no property tax receipt was supplied")
        return

    newest = None
    for observation in receipts:
        parsed = parse_date(observation.value)
        if parsed.ok and (newest is None or parsed.value > newest[0]):
            newest = (parsed.value, observation)

    if newest is None:
        yield RuleOutcome(
            rule_id=rule_id,
            category="land",
            result=RuleResult.REVIEW,
            field="receipt_date",
            severity=severity,
            reason="the tax receipt date could not be read",
            evidence=[receipts[0].as_evidence()],
        )
        return

    paid_on, observation = newest
    age = (context.as_of - paid_on).days / 365.25
    if age <= max_age:
        yield RuleOutcome(
            rule_id=rule_id,
            category="land",
            result=RuleResult.PASS,
            field="receipt_date",
            reason=f"property tax was paid on {observation.value}",
            evidence=[observation.as_evidence()],
        )
        return

    summary = (
        f"the most recent property tax receipt is from {observation.value}, "
        f"{age:.0f} years old. It does not show that the current dues are clear."
    )
    yield RuleOutcome(
        rule_id=rule_id,
        category="land",
        result=RuleResult.FAIL,
        field="receipt_date",
        severity=severity,
        reason=summary,
        evidence=[observation.as_evidence()],
        candidate=CandidateDiscrepancy(
            type="PROPERTY_TAX_STALE",
            field="receipt_date",
            severity=severity,
            rule_id=rule_id,
            origin="RULE_ENGINE",
            comparison_method="date",
            summary=summary,
            values=[observation.value],
            evidence=[observation.as_evidence()],
            needs_reasoning=False,
            deterministic=True,
        ),
    )


# --------------------------------------------------------------------------
# The certificate read as a ledger
# --------------------------------------------------------------------------
def _ledger_transfers(ledger) -> list[dict]:  # noqa: ANN001
    """The rows of a certificate that actually move ownership.

    A mortgage, its release and a lease all sit in the same table as a sale,
    and only a sale changes who owns the land. Treating a mortgage as a link
    would break the chain of every property that ever carried a loan.
    """
    from app.schemas.extraction import RegisteredTransaction

    transfers = []
    for row in ledger.transactions:
        try:
            parsed = RegisteredTransaction.model_validate(row)
        except Exception:  # noqa: BLE001 - a malformed row is not a failed rule
            continue
        if parsed.is_transfer and parsed.executant and parsed.claimant:
            transfers.append(
                {
                    "executant": parsed.executant,
                    "claimant": parsed.claimant,
                    "date": parsed.date,
                    "nature": parsed.nature,
                    "iso": (parse_date(parsed.date).iso or "") if parsed.date else "",
                }
            )
    return sorted(transfers, key=lambda t: t["iso"] or "9999-99-99")


@rule("land", "ledger_chain")
def ledger_chain(context: RuleContext) -> Iterable[RuleOutcome]:
    """Walk the chain recorded inside the encumbrance certificate itself.

    This is the check the deeds cannot make. An applicant supplies the deed
    that gave them the property and, quite reasonably, does not hold the deeds
    of the people who owned it before — so a chain built from deeds alone is
    only ever as long as the paperwork that happened to survive. The
    certificate lists every registered transfer over its period, which means a
    break in the middle of the history shows up here even when no deed for it
    was ever supplied.
    """
    rule_id = "land.ledger_chain"
    severity = context.config.severity("land", "ledger_chain", Severity.HIGH)

    if not context.ledgers:
        yield _not_applicable(rule_id, "no encumbrance certificate was read as a ledger")
        return

    thresholds = _thresholds(context)

    for ledger in context.ledgers:
        transfers = _ledger_transfers(ledger)
        if len(transfers) < 2:
            yield RuleOutcome(
                rule_id=rule_id,
                category="land",
                result=RuleResult.NOT_APPLICABLE,
                field="ownership_chain",
                reason=(
                    f"{ledger.document_name} records {len(transfers)} transfer(s); "
                    "a chain needs at least two"
                ),
            )
            continue

        breaks = 0
        for previous, current in zip(transfers, transfers[1:], strict=False):
            outcome = compare_field(
                "name", previous["claimant"], current["executant"], thresholds=thresholds
            )
            if outcome.verdict == ComparisonVerdict.EQUAL:
                continue

            breaks += 1
            undecided = outcome.verdict != ComparisonVerdict.DIFFERENT
            summary = (
                f"{ledger.document_name} records the property passing to "
                f"{previous['claimant']} on {previous['date']}, and the next "
                f"transfer on {current['date']} is made by {current['executant']}. "
                "The certificate does not account for how it passed between them."
            )
            yield RuleOutcome(
                rule_id=rule_id,
                category="land",
                result=RuleResult.REVIEW if undecided else RuleResult.FAIL,
                field="ownership_chain",
                severity=severity,
                reason=summary,
                candidate=CandidateDiscrepancy(
                    type="OWNERSHIP_CHAIN_BREAK",
                    field="ownership_chain",
                    severity=severity,
                    rule_id=rule_id,
                    origin="RULE_ENGINE",
                    comparison_method="name",
                    summary=summary,
                    values=[previous["claimant"], current["executant"]],
                    evidence=[],
                    needs_reasoning=undecided,
                    deterministic=not undecided,
                ),
            )

        if not breaks:
            yield RuleOutcome(
                rule_id=rule_id,
                category="land",
                result=RuleResult.PASS,
                field="ownership_chain",
                reason=(
                    f"{ledger.document_name} records {len(transfers)} transfers running "
                    f"unbroken from {transfers[0]['executant']} to {transfers[-1]['claimant']}"
                ),
            )


@rule("land", "deeds_agree_with_ledger")
def deeds_agree_with_ledger(context: RuleContext) -> Iterable[RuleOutcome]:
    """A deed that the registrar's own record does not mention.

    The certificate is the registry's account of what was registered against
    this property. A deed the applicant supplies that has no counterpart in it
    is the more serious direction of disagreement: an unregistered or forged
    conveyance looks exactly like this.
    """
    rule_id = "land.deeds_agree_with_ledger"
    severity = context.config.severity("land", "deeds_agree_with_ledger", Severity.HIGH)
    deeds = [t for t in _transfers(context) if t.describes_a_transfer]

    if not deeds or not context.ledgers:
        yield _not_applicable(
            rule_id, "needs both a sale deed and an encumbrance certificate"
        )
        return

    recorded = [row for ledger in context.ledgers for row in _ledger_transfers(ledger)]
    if not recorded:
        yield _not_applicable(rule_id, "the certificate records no transfers to compare")
        return

    thresholds = _thresholds(context)
    unmatched = 0
    for deed in deeds:
        assert deed.seller and deed.buyer
        matched = any(
            compare_field(
                "name", deed.seller.value, row["executant"], thresholds=thresholds
            ).verdict
            == ComparisonVerdict.EQUAL
            and compare_field(
                "name", deed.buyer.value, row["claimant"], thresholds=thresholds
            ).verdict
            == ComparisonVerdict.EQUAL
            for row in recorded
        )
        if matched:
            continue

        unmatched += 1
        summary = (
            f"{deed.document_name} records a transfer from {deed.seller.value} to "
            f"{deed.buyer.value}, which does not appear in the encumbrance "
            "certificate for this property."
        )
        evidence = [deed.seller.as_evidence(), deed.buyer.as_evidence()]
        yield RuleOutcome(
            rule_id=rule_id,
            category="land",
            result=RuleResult.REVIEW,
            field="ownership_chain",
            severity=severity,
            reason=summary,
            evidence=evidence,
            candidate=CandidateDiscrepancy(
                type="DEED_NOT_IN_ENCUMBRANCE_RECORD",
                field="ownership_chain",
                severity=severity,
                rule_id=rule_id,
                origin="RULE_ENGINE",
                comparison_method="name",
                summary=summary,
                values=[deed.seller.value, deed.buyer.value],
                evidence=evidence,
                # A deed registered outside the certificate's period is the
                # innocent explanation and is common, so this is surfaced for
                # judgement rather than asserted.
                needs_reasoning=True,
                deterministic=False,
            ),
        )

    if not unmatched:
        yield RuleOutcome(
            rule_id=rule_id,
            category="land",
            result=RuleResult.PASS,
            field="ownership_chain",
            reason=(
                f"all {len(deeds)} supplied deed(s) appear in the encumbrance record"
            ),
        )
