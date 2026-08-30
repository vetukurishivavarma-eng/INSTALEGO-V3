"""Land title rules: the chain, the parcel, the owner, the charges.

The chain is the part worth testing hardest. A break in it is the classic
signature of a defective title, and it is exactly the kind of finding that has
to be right in both directions: missing one is a bank lending against land the
applicant may not own, and inventing one stops a sound application dead.

Two properties get their own tests because getting them wrong is quiet rather
than loud. A previous owner must never reach the applicant profile — otherwise
every honest chain of title manufactures a name conflict. And a khata standing
in someone else's name must be reported as a land finding, not as the applicant
being a different person.
"""

from __future__ import annotations

from datetime import date

from app.models.enums import RuleResult, Severity
from app.rules import DocumentView, FieldObservation, RuleContext, RuleEngine, load_rule_config
from app.agents.profile_builder import collect_candidates, consolidate

REVIEW_DATE = date(2026, 8, 29)

APPLICANT = "Ravi Kumar"
SURVEY = "42/1B"


def observation(field, value, doc_id, doc_name, doc_type, page=1):
    from app.utils.normalize import normalize_field

    return FieldObservation(
        canonical_field=field,
        raw_field=field,
        value=value,
        normalized_value=normalize_field(field, value).normalized,
        confidence=0.95,
        document_id=doc_id,
        document_name=doc_name,
        document_type=doc_type,
        page=page,
        snippet=f"{field}: {value}",
    )


def deed(doc_id, name, seller, buyer, registered, survey=SURVEY):
    """One registered transfer, as a document plus its extracted fields."""
    view = DocumentView(
        document_id=doc_id, filename=name, document_type="SALE_DEED",
        sha256=doc_id * 8, page_count=4,
    )
    fields = [
        observation("seller_name", seller, doc_id, name, "SALE_DEED"),
        observation("buyer_name", buyer, doc_id, name, "SALE_DEED"),
        observation("registration_date", registered, doc_id, name, "SALE_DEED"),
    ]
    if survey:
        fields.append(observation("survey_number", survey, doc_id, name, "SALE_DEED"))
    return view, fields


def build_context(pairs, extra_observations=(), bank_id="bank_a", applicant=APPLICANT):
    documents = [view for view, _ in pairs]
    observations = [o for _, fields in pairs for o in fields] + list(extra_observations)
    if applicant:
        documents.append(
            DocumentView(document_id="id1", filename="Aadhaar.pdf",
                         document_type="AADHAAR", sha256="a" * 64, page_count=1)
        )
        observations.append(observation("name", applicant, "id1", "Aadhaar.pdf", "AADHAAR"))

    candidates = collect_candidates([
        {
            "document_id": o.document_id,
            "document_name": o.document_name,
            "document_type": o.document_type,
            "fields": [{
                "canonical_field": o.canonical_field,
                "value": o.value,
                "confidence": o.confidence,
                "page": o.page,
                "snippet": o.snippet,
            }],
        }
        for o in observations
    ])
    return RuleContext(
        profile=consolidate(candidates),
        documents=documents,
        observations=observations,
        config=load_rule_config(bank_id),
        as_of=REVIEW_DATE,
    )


def run(context):
    engine = RuleEngine(context.config)
    outcomes = engine.run(context)
    return outcomes, RuleEngine.candidates(outcomes)


def findings_of_type(candidates, type_name):
    return [c for c in candidates if c.type == type_name]


def outcomes_for(outcomes, rule_id):
    return [o for o in outcomes if o.rule_id == rule_id]


# A clean history: three transfers ending with the applicant.
UNBROKEN = [
    deed("d1", "SaleDeed2015.pdf", "Anil Sharma", "Meera Reddy", "14/03/2015"),
    deed("d2", "SaleDeed2019.pdf", "Meera Reddy", "Suresh Kumar", "02/09/2019"),
    deed("d3", "SaleDeed2024.pdf", "Suresh Kumar", APPLICANT, "21/06/2024"),
]


class TestOwnershipChain:
    def test_an_unbroken_chain_passes(self):
        outcomes, candidates = run(build_context(UNBROKEN))
        assert findings_of_type(candidates, "OWNERSHIP_CHAIN_BREAK") == []
        chain = outcomes_for(outcomes, "land.ownership_chain")
        assert any(o.result == RuleResult.PASS for o in chain), [o.reason for o in chain]

    def test_a_missing_transfer_is_a_high_finding(self):
        """The 2019 buyer is not the 2024 seller, so one conveyance is
        unaccounted for. This is the finding the whole feature exists for."""
        broken = [
            UNBROKEN[0],
            UNBROKEN[1],
            deed("d3", "SaleDeed2024.pdf", "Priya Nair", APPLICANT, "21/06/2024"),
        ]
        _, candidates = run(build_context(broken))
        findings = findings_of_type(candidates, "OWNERSHIP_CHAIN_BREAK")
        assert len(findings) == 1
        assert findings[0].severity == Severity.HIGH
        assert "Suresh Kumar" in findings[0].summary
        assert "Priya Nair" in findings[0].summary

    def test_the_chain_is_read_in_registration_order_not_upload_order(self):
        """Deeds arrive in whatever order they were dragged in."""
        shuffled = [UNBROKEN[2], UNBROKEN[0], UNBROKEN[1]]
        _, candidates = run(build_context(shuffled))
        assert findings_of_type(candidates, "OWNERSHIP_CHAIN_BREAK") == []

    def test_a_spelling_difference_between_deeds_is_not_a_break(self):
        """Deeds decades apart spell the same person differently. Flagging that
        as a defective title would make the check unusable."""
        tolerant = [
            deed("d1", "SaleDeed2015.pdf", "Anil Sharma", "Meera Reddy", "14/03/2015"),
            deed("d2", "SaleDeed2019.pdf", "MEERA REDDY", "Suresh Kumar", "02/09/2019"),
            deed("d3", "SaleDeed2024.pdf", "Suresh Kumar", APPLICANT, "21/06/2024"),
        ]
        _, candidates = run(build_context(tolerant))
        assert findings_of_type(candidates, "OWNERSHIP_CHAIN_BREAK") == []

    def test_a_single_deed_is_not_a_chain(self):
        outcomes, candidates = run(build_context([UNBROKEN[2]]))
        assert findings_of_type(candidates, "OWNERSHIP_CHAIN_BREAK") == []
        chain = outcomes_for(outcomes, "land.ownership_chain")
        assert all(o.result == RuleResult.NOT_APPLICABLE for o in chain)

    def test_no_land_documents_produces_no_finding(self):
        """Absence is report coverage, never a discrepancy."""
        outcomes, candidates = run(build_context([]))
        assert [c for c in candidates if c.rule_id.startswith("land.")] == []
        land = [o for o in outcomes if o.category == "land"]
        assert land and all(o.result == RuleResult.NOT_APPLICABLE for o in land)

    def test_an_undated_deed_is_reported_rather_than_assumed_into_place(self):
        undated = [
            UNBROKEN[0],
            UNBROKEN[1],
            deed("d3", "SaleDeed.pdf", "Suresh Kumar", APPLICANT, ""),
        ]
        outcomes, _ = run(build_context(undated))
        chain = outcomes_for(outcomes, "land.ownership_chain")
        assert any(o.result == RuleResult.REVIEW and "registration date" in o.reason
                   for o in chain), [o.reason for o in chain]

    def test_two_deeds_registered_the_same_day_are_flagged_as_ambiguous(self):
        same_day = [
            deed("d1", "A.pdf", "Anil Sharma", "Meera Reddy", "14/03/2015"),
            deed("d2", "B.pdf", "Meera Reddy", APPLICANT, "14/03/2015"),
        ]
        _, candidates = run(build_context(same_day))
        assert findings_of_type(candidates, "CHAIN_DATES_AMBIGUOUS")


class TestTheChainDoesNotContaminateTheApplicant:
    def test_previous_owners_never_reach_the_applicant_profile(self):
        """If seller_name folded into `name`, every honest chain of title would
        raise a NAME_MISMATCH against the applicant."""
        context = build_context(UNBROKEN)
        assert context.profile.fields["name"].value
        _, candidates = run(context)
        assert findings_of_type(candidates, "NAME_MISMATCH") == []

    def test_a_khata_in_someone_elses_name_is_a_land_finding_not_an_identity_one(self):
        """A pending mutation is normal. It is worth reporting, but as a fact
        about the property rather than as the applicant being someone else."""
        khata = DocumentView(document_id="k1", filename="Khata.pdf",
                             document_type="KHATA_CERTIFICATE", sha256="k" * 64, page_count=1)
        fields = [
            observation("property_owner_name", "Suresh Kumar", "k1", "Khata.pdf",
                        "KHATA_CERTIFICATE"),
            observation("survey_number", SURVEY, "k1", "Khata.pdf", "KHATA_CERTIFICATE"),
        ]
        _, candidates = run(build_context([*UNBROKEN, (khata, fields)]))
        assert findings_of_type(candidates, "NAME_MISMATCH") == []
        found = findings_of_type(candidates, "PROPERTY_NOT_IN_APPLICANT_NAME")
        assert len(found) == 1
        assert "Suresh Kumar" in found[0].summary


class TestPropertyIdentity:
    def test_two_survey_numbers_are_two_parcels_of_land(self):
        mixed = [
            UNBROKEN[0],
            deed("d2", "SaleDeed2019.pdf", "Meera Reddy", APPLICANT, "02/09/2019",
                 survey="88/2A"),
        ]
        _, candidates = run(build_context(mixed))
        findings = findings_of_type(candidates, "PROPERTY_IDENTITY_MISMATCH")
        assert len(findings) == 1
        assert findings[0].severity == Severity.HIGH

    def test_one_parcel_across_every_document_passes(self):
        _, candidates = run(build_context(UNBROKEN))
        assert findings_of_type(candidates, "PROPERTY_IDENTITY_MISMATCH") == []


class TestEncumbranceAndTax:
    def _ec(self, status, frm="01/01/2013", to="01/07/2026"):
        view = DocumentView(document_id="e1", filename="EC.pdf",
                            document_type="ENCUMBRANCE_CERTIFICATE",
                            sha256="e" * 64, page_count=2)
        return view, [
            observation("encumbrance_status", status, "e1", "EC.pdf", "ENCUMBRANCE_CERTIFICATE"),
            observation("ec_period_from", frm, "e1", "EC.pdf", "ENCUMBRANCE_CERTIFICATE"),
            observation("ec_period_to", to, "e1", "EC.pdf", "ENCUMBRANCE_CERTIFICATE"),
        ]

    def test_a_nil_certificate_passes(self):
        _, candidates = run(build_context([*UNBROKEN, self._ec("NIL")]))
        assert findings_of_type(candidates, "PROPERTY_ENCUMBERED") == []

    def test_a_subsisting_mortgage_is_high(self):
        _, candidates = run(build_context(
            [*UNBROKEN, self._ec("Mortgage registered in favour of Canara Bank, 2021")]
        ))
        findings = findings_of_type(candidates, "PROPERTY_ENCUMBERED")
        assert len(findings) == 1
        assert findings[0].severity == Severity.HIGH

    def test_unfamiliar_wording_is_sent_for_a_second_opinion(self):
        """What an unrecognised phrase amounts to is not the rule engine's
        call, so it is surfaced rather than decided."""
        _, candidates = run(build_context([*UNBROKEN, self._ec("Charge released 2022")]))
        findings = findings_of_type(candidates, "PROPERTY_ENCUMBERED")
        assert findings and findings[0].needs_reasoning

    def test_a_certificate_covering_too_few_years_is_reported(self):
        _, candidates = run(build_context(
            [*UNBROKEN, self._ec("NIL", frm="01/01/2023", to="01/07/2026")]
        ))
        findings = findings_of_type(candidates, "ENCUMBRANCE_PERIOD_SHORT")
        assert len(findings) == 1
        assert "13" in findings[0].summary

    def test_a_stale_tax_receipt_is_low_not_high(self):
        """Out-of-date tax is worth mentioning and is not a title defect."""
        view = DocumentView(document_id="t1", filename="Tax.pdf",
                            document_type="PROPERTY_TAX_RECEIPT", sha256="t" * 64, page_count=1)
        fields = [observation("receipt_date", "10/05/2019", "t1", "Tax.pdf",
                              "PROPERTY_TAX_RECEIPT")]
        _, candidates = run(build_context([*UNBROKEN, (view, fields)]))
        findings = findings_of_type(candidates, "PROPERTY_TAX_STALE")
        assert len(findings) == 1
        assert findings[0].severity == Severity.LOW

    def test_a_recent_tax_receipt_passes(self):
        view = DocumentView(document_id="t1", filename="Tax.pdf",
                            document_type="PROPERTY_TAX_RECEIPT", sha256="t" * 64, page_count=1)
        fields = [observation("receipt_date", "10/05/2026", "t1", "Tax.pdf",
                              "PROPERTY_TAX_RECEIPT")]
        _, candidates = run(build_context([*UNBROKEN, (view, fields)]))
        assert findings_of_type(candidates, "PROPERTY_TAX_STALE") == []


class TestOnlyDeedsBuildTheChain:
    def test_a_tax_receipt_cannot_contribute_a_transfer(self):
        """Only a deed records a conveyance. If a misclassified tax receipt
        could inject seller/buyer, it would fabricate a transfer that never
        happened and break a sound chain."""
        receipt = DocumentView(document_id="t1", filename="Tax.pdf",
                               document_type="PROPERTY_TAX_RECEIPT",
                               sha256="t" * 64, page_count=1)
        fields = [
            observation("seller_name", "Someone Else", "t1", "Tax.pdf", "PROPERTY_TAX_RECEIPT"),
            observation("buyer_name", "Another Person", "t1", "Tax.pdf", "PROPERTY_TAX_RECEIPT"),
        ]
        _, candidates = run(build_context([*UNBROKEN, (receipt, fields)]))
        assert findings_of_type(candidates, "OWNERSHIP_CHAIN_BREAK") == []
