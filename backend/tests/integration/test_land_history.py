"""Land history end to end: with the documents, and without them.

Two halves, and the second is as important as the first. When land documents
arrive, the chain of title is walked and a break is reported. When they do not
arrive, nothing is wrong — the report simply does not cover the land, and it
has to say so and say what would cover it. Conflating those two is how a
personal loan ends up flagged HIGH for not supplying a title deed.

These run against the stub, which extracts by matching label/value lines. That
is enough to exercise every deterministic layer: classification by keyword,
canonical mapping, the chain walk, and the report section. What it cannot tell
you is whether a real model reads a real deed correctly.
"""

from __future__ import annotations

import pytest

from app.models.enums import Severity
from tests.fixtures.builders import make_land_pack

BROKEN_CHAIN = (
    ("Anil Sharma", "Meera Reddy", "14/03/2015"),
    ("Meera Reddy", "Suresh Kumar", "02/09/2019"),
    # Priya Nair never bought it from Suresh Kumar: one transfer is missing.
    ("Priya Nair", "Ravi Kumar", "21/06/2024"),
)


def create_case(client, bank_id="bank_a", applicant="Ravi Kumar"):
    response = client.post(
        "/api/cases", json={"bank_id": bank_id, "applicant_name": applicant}
    )
    assert response.status_code == 201, response.text
    return response.json()


def upload(client, case_id, files: dict):
    payload = [
        ("files", (path.name, path.read_bytes(), "application/octet-stream"))
        for path in files.values()
    ]
    response = client.post(f"/api/cases/{case_id}/documents", files=payload)
    assert response.status_code == 200, response.text
    return response.json()


def analyse(client, case_id):
    response = client.post(f"/api/cases/{case_id}/analyze")
    assert response.status_code == 200, response.text
    analysis = client.get(f"/api/cases/{case_id}/analysis")
    assert analysis.status_code == 200, analysis.text
    return analysis.json()


def coverage_for(analysis, key="LAND_TITLE"):
    for item in analysis["completeness"]:
        if item["key"] == key:
            return item
    raise AssertionError(f"no coverage entry for {key}: {analysis['completeness']}")


def findings_of(analysis, type_name):
    return [d for d in analysis["discrepancies"] if d["type"] == type_name]


class TestWithoutLandDocuments:
    @pytest.fixture
    def analysed(self, client, consistent_case_files):
        case = create_case(client)
        upload(client, case["id"], consistent_case_files)
        return analyse(client, case["id"])

    def test_the_report_says_what_to_upload(self, analysed):
        coverage = coverage_for(analysed)
        assert coverage["satisfied"] is False
        assert coverage["provided"] == []
        assert "upload" in coverage["message"].lower()
        assert "sale deed" in coverage["message"].lower()

    def test_absence_is_not_a_finding(self, analysed):
        """The whole point of keeping coverage separate from discrepancies. A
        personal loan supplies no title deed and nothing about that is wrong."""
        land_findings = [
            d for d in analysed["discrepancies"]
            if d["type"].startswith(("OWNERSHIP_", "PROPERTY_", "CHAIN_", "ENCUMBRANCE_"))
        ]
        assert land_findings == []

    def test_absence_does_not_move_the_case_status(self, analysed):
        assert analysed["final_status"] == "CLEAR"

    def test_land_documents_are_not_reported_as_missing_required_ones(self, analysed):
        """bank_a does not require them, so they must not appear in the table
        that means "the bank asked for this and did not get it"."""
        missing = {item["document_type"] for item in analysed["missing_documents"]}
        assert not missing & {"SALE_DEED", "PROPERTY_DOCUMENT", "ENCUMBRANCE_CERTIFICATE"}


class TestWithAnUnbrokenChain:
    @pytest.fixture
    def analysed(self, client, consistent_case_files, tmp_path):
        case = create_case(client)
        upload(client, case["id"], consistent_case_files)
        upload(client, case["id"], make_land_pack(tmp_path / "land"))
        return analyse(client, case["id"])

    def test_the_coverage_section_reports_the_land_as_covered(self, analysed):
        coverage = coverage_for(analysed)
        assert coverage["satisfied"] is True, coverage
        assert coverage["awaiting"] == []

    def test_a_sound_chain_raises_nothing(self, analysed):
        assert findings_of(analysed, "OWNERSHIP_CHAIN_BREAK") == []
        assert findings_of(analysed, "PROPERTY_IDENTITY_MISMATCH") == []
        assert findings_of(analysed, "PROPERTY_ENCUMBERED") == []

    def test_previous_owners_stay_out_of_the_applicant_profile(self, analysed):
        """Meera Reddy and Anil Sharma owned this land. Neither is the
        applicant, and neither may contradict who the applicant is."""
        assert findings_of(analysed, "NAME_MISMATCH") == []
        name = analysed["applicant"]["fields"]["name"]["value"]
        assert "Ravi" in name


class TestWithABrokenChain:
    @pytest.fixture
    def analysed(self, client, consistent_case_files, tmp_path):
        case = create_case(client)
        upload(client, case["id"], consistent_case_files)
        upload(client, case["id"], make_land_pack(tmp_path / "land", chain=BROKEN_CHAIN))
        return analyse(client, case["id"])

    def test_the_missing_transfer_is_reported(self, analysed):
        findings = findings_of(analysed, "OWNERSHIP_CHAIN_BREAK")
        assert len(findings) == 1, analysed["discrepancies"]
        assert findings[0]["severity"] == Severity.HIGH

    def test_the_finding_names_both_parties_and_cites_the_deeds(self, analysed):
        finding = findings_of(analysed, "OWNERSHIP_CHAIN_BREAK")[0]
        assert "Suresh Kumar" in finding["explanation"]
        assert "Priya Nair" in finding["explanation"]
        cited = {reference["document_name"] for reference in finding["evidence"]}
        assert len(cited) == 2, finding["evidence"]

    def test_a_broken_title_sends_the_case_to_review(self, analysed):
        assert analysed["final_status"] in {"REVIEW_REQUIRED", "HIGH_RISK"}


class TestWithAnEncumberedProperty:
    @pytest.fixture
    def analysed(self, client, consistent_case_files, tmp_path):
        case = create_case(client)
        upload(client, case["id"], consistent_case_files)
        upload(
            client,
            case["id"],
            make_land_pack(
                tmp_path / "land",
                encumbrance="Mortgage registered in favour of Canara Bank on 12/08/2021",
            ),
        )
        return analyse(client, case["id"])

    def test_a_subsisting_charge_is_reported(self, analysed):
        """Asserted on the finding and its evidence, not on its prose.

        An unfamiliar encumbrance wording is marked needs_reasoning, so the
        explanation is written by the reasoning agent — which under the stub is
        a placeholder. The evidence is produced deterministically and is what
        actually has to be right.
        """
        findings = findings_of(analysed, "PROPERTY_ENCUMBERED")
        assert len(findings) == 1, analysed["discrepancies"]

        # Severity is not asserted here either: the reasoning agent may
        # downgrade a candidate it was asked to assess, and under the stub it
        # always does. The configured HIGH is covered against the rule itself
        # in tests/unit/test_land_rules.py.
        cited = findings[0]["evidence"]
        assert cited, findings[0]
        assert any("Encumbrance" in reference["document_name"] for reference in cited), cited
        assert any("Canara" in reference["value"] for reference in cited), cited


class TestWithAPendingMutation:
    @pytest.fixture
    def analysed(self, client, consistent_case_files, tmp_path):
        case = create_case(client)
        upload(client, case["id"], consistent_case_files)
        upload(
            client,
            case["id"],
            # The deeds end with the applicant, but the municipal record still
            # stands in the seller's name: a mutation that was never carried
            # through. Common, and worth reporting as a property fact.
            make_land_pack(tmp_path / "land", khata_owner="Suresh Kumar"),
        )
        return analyse(client, case["id"])

    def test_it_is_reported_against_the_property_not_the_person(self, analysed):
        assert findings_of(analysed, "NAME_MISMATCH") == []
        findings = findings_of(analysed, "PROPERTY_NOT_IN_APPLICANT_NAME")
        assert findings, analysed["discrepancies"]
        assert "Suresh Kumar" in findings[0]["explanation"]


class TestTheReportRendersCoverage:
    def test_the_coverage_section_reaches_the_generated_report(
        self, client, consistent_case_files
    ):
        case = create_case(client)
        upload(client, case["id"], consistent_case_files)
        analyse(client, case["id"])

        response = client.post(f"/api/cases/{case['id']}/reports/generate", json={})
        assert response.status_code == 200, response.text
        sections = response.json()["report_json"]
        assert "report_coverage" in sections, list(sections)

        rows = sections["report_coverage"]
        rows = rows["rows"] if isinstance(rows, dict) else rows
        assert rows, sections["report_coverage"]
        assert any("upload" in str(row).lower() for row in rows), rows
