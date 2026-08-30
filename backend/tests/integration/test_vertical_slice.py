"""The end-to-end slice: case, upload, analysis, flags, report.

This is the acceptance test for the pipeline as a whole. It runs against the
mock model client, so what it proves is that ingestion, parsing, normalisation,
the rule engine, comparison, the canonical analysis, report generation and QA
all fit together and produce evidence-backed findings from real files. What the
real model extracts is the evaluation suite's job, not this one's.
"""

from __future__ import annotations

import pytest

from app.models.enums import CaseStatus, OverallStatus, Severity


def create_case(client, bank_id="bank_a", applicant="Ravi Kumar"):
    response = client.post(
        "/api/cases", json={"bank_id": bank_id, "applicant_name": applicant}
    )
    assert response.status_code == 201, response.text
    return response.json()


def upload(client, case_id, files: dict, analyze=False):
    payload = [
        ("files", (path.name, path.read_bytes(), "application/octet-stream"))
        for path in files.values()
    ]
    response = client.post(
        f"/api/cases/{case_id}/documents", files=payload, params={"analyze": analyze}
    )
    assert response.status_code == 200, response.text
    return response.json()


def analyse(client, case_id):
    response = client.post(f"/api/cases/{case_id}/analyze")
    assert response.status_code == 200, response.text
    return response.json()


class TestCaseAndUpload:
    def test_case_creation_assigns_a_readable_reference(self, client):
        case = create_case(client)
        assert case["case_ref"].startswith("CASE-")
        assert case["status"] == CaseStatus.CREATED
        assert case["bank_id"] == "bank_a"

    def test_every_supported_format_is_accepted(self, client, consistent_case_files):
        case = create_case(client)
        result = upload(client, case["id"], consistent_case_files)
        assert result["accepted"] == 5
        assert result["rejected"] == 0

    def test_an_unsupported_file_is_rejected_without_failing_the_batch(
        self, client, consistent_case_files, tmp_path
    ):
        case = create_case(client)
        bad = tmp_path / "notes.txt"
        bad.write_text("not a document")

        files = {**consistent_case_files, "bad": bad}
        result = upload(client, case["id"], files)

        assert result["accepted"] == 5
        assert result["rejected"] == 1
        rejected = [r for r in result["results"] if not r["accepted"]][0]
        assert rejected["error_code"] == "UNSUPPORTED_FILE"

    def test_documents_are_hashed_and_stored(self, client, consistent_case_files):
        case = create_case(client)
        upload(client, case["id"], consistent_case_files)

        documents = client.get(f"/api/cases/{case['id']}/documents").json()
        assert len(documents) == 5
        for document in documents:
            assert len(document["sha256"]) == 64
            assert document["size_bytes"] > 0

    def test_the_original_can_be_downloaded_unchanged(self, client, consistent_case_files):
        case = create_case(client)
        upload(client, case["id"], consistent_case_files)
        documents = client.get(f"/api/cases/{case['id']}/documents").json()

        target = next(d for d in documents if d["filename"] == "Aadhaar.pdf")
        response = client.get(f"/api/documents/{target['id']}/file")
        assert response.status_code == 200
        assert response.content == consistent_case_files["aadhaar"].read_bytes()


class TestAnalysisOnConsistentDocuments:
    @pytest.fixture
    def analysed(self, client, consistent_case_files):
        case = create_case(client)
        upload(client, case["id"], consistent_case_files)
        analyse(client, case["id"])
        return client.get(f"/api/cases/{case['id']}/analysis").json()

    def test_documents_are_classified(self, analysed):
        types = {d["document_type"] for d in analysed["documents"]}
        assert "AADHAAR" in types
        assert "PAN" in types
        assert "LOAN_APPLICATION" in types

    def test_fields_are_extracted_into_a_profile(self, analysed):
        profile = analysed["applicant"]["fields"]
        assert profile["name"]["value"] == "Ravi Kumar"
        assert profile["date_of_birth"]["status"] == "CONFIRMED"
        assert profile["pan"]["value"] == "ABCDE1234F"

    def test_every_profile_value_keeps_its_sources(self, analysed):
        for name, field in analysed["applicant"]["fields"].items():
            if field["status"] in {"CONFIRMED", "CONFLICTING"}:
                assert field["sources"], f"{name} has no source evidence"
                for source in field["sources"]:
                    assert source["document_name"]
                    assert source["page"] >= 1

    def test_normalised_values_are_stored_alongside_the_originals(self, analysed):
        dob = analysed["applicant"]["fields"]["date_of_birth"]
        assert dob["value"] == "12/04/1998"
        assert dob["normalized_value"] == "1998-04-12"

    def test_no_identity_discrepancies_are_raised(self, analysed):
        types = {d["type"] for d in analysed["discrepancies"]}
        assert "DOB_MISMATCH" not in types
        assert "NAME_MISMATCH" not in types
        assert "PAN_MISMATCH" not in types
        assert "ADDRESS_MISMATCH" not in types

    def test_validations_record_passes_not_only_failures(self, analysed):
        results = {v["result"] for v in analysed["validations"]}
        assert "PASS" in results
        assert len(analysed["validations"]) > 10

    def test_provenance_is_recorded(self, analysed):
        versions = analysed["versions"]
        assert versions["analysis_version"]
        assert versions["rules_version"] == "bank-a-v1"
        assert versions["model"]


class TestAnalysisOnConflictingDocuments:
    @pytest.fixture
    def analysed(self, client, conflicting_case_files):
        case = create_case(client)
        upload(client, case["id"], conflicting_case_files)
        analyse(client, case["id"])
        return client.get(f"/api/cases/{case['id']}/analysis").json()

    def test_dob_mismatch_is_detected_as_high(self, analysed):
        findings = [d for d in analysed["discrepancies"] if d["type"] == "DOB_MISMATCH"]
        assert len(findings) == 1
        assert findings[0]["severity"] == Severity.HIGH

    def test_pan_mismatch_is_detected_as_high(self, analysed):
        findings = [d for d in analysed["discrepancies"] if d["type"] == "PAN_MISMATCH"]
        assert len(findings) == 1
        assert findings[0]["severity"] == Severity.HIGH

    def test_the_name_variation_is_not_flagged(self, analysed):
        # Ravi Kumar against Ravi K Kumar is the same person written twice.
        assert [d for d in analysed["discrepancies"] if d["type"] == "NAME_MISMATCH"] == []

    def test_address_mismatch_is_medium_for_this_bank(self, analysed):
        findings = [d for d in analysed["discrepancies"] if d["type"] == "ADDRESS_MISMATCH"]
        assert len(findings) == 1
        assert findings[0]["severity"] == Severity.MEDIUM

    def test_every_finding_cites_two_documents_and_two_pages(self, analysed):
        for finding in analysed["discrepancies"]:
            if finding["type"] in {"DOB_MISMATCH", "PAN_MISMATCH", "ADDRESS_MISMATCH"}:
                evidence = finding["evidence"]
                assert len(evidence) == 2, finding["type"]
                for reference in evidence:
                    assert reference["document_name"]
                    assert reference["page"] >= 1
                    assert reference["value"]

    def test_evidence_values_differ_and_match_the_documents(self, analysed):
        dob = next(d for d in analysed["discrepancies"] if d["type"] == "DOB_MISMATCH")
        values = {reference["value"] for reference in dob["evidence"]}
        assert values == {"12/04/1998", "12/04/1997"}

    def test_the_profile_preserves_both_conflicting_values(self, analysed):
        dob = analysed["applicant"]["fields"]["date_of_birth"]
        assert dob["status"] == "CONFLICTING"
        assert sorted(dob["candidates"]) == ["12/04/1997", "12/04/1998"]

    def test_status_requires_review(self, analysed):
        assert analysed["final_status"] in {
            OverallStatus.REVIEW_REQUIRED,
            OverallStatus.HIGH_RISK,
        }
        assert analysed["manual_review_required"] is True

    def test_missing_documents_are_reported(self, analysed):
        missing = {m["document_type"] for m in analysed["missing_documents"]}
        assert "INCOME_PROOF" in missing
        assert "BANK_STATEMENT" in missing

    def test_no_finding_alleges_fraud(self, analysed):
        banned = ("fraud", "forged", "fake", "falsified", "criminal")
        for finding in analysed["discrepancies"]:
            text = f"{finding['explanation']} {finding['recommended_action']}".lower()
            assert not any(word in text for word in banned), finding["explanation"]


class TestReviewAndAudit:
    @pytest.fixture
    def case_id(self, client, conflicting_case_files):
        case = create_case(client)
        upload(client, case["id"], conflicting_case_files)
        analyse(client, case["id"])
        return case["id"]

    def test_a_flag_can_be_reviewed_without_deleting_it(self, client, case_id):
        findings = client.get(f"/api/cases/{case_id}/discrepancies").json()
        target = findings[0]

        response = client.post(
            f"/api/cases/{case_id}/discrepancies/{target['code']}/review",
            json={"decision": "ACCEPTED", "note": "confirmed against the original"},
        )
        assert response.status_code == 200
        assert response.json()["review_decision"] == "ACCEPTED"

        after = client.get(f"/api/cases/{case_id}/discrepancies").json()
        assert len(after) == len(findings)

    def test_the_audit_trail_covers_upload_through_analysis(self, client, case_id):
        trail = client.get(f"/api/cases/{case_id}/audit").json()
        actions = {entry["action"] for entry in trail}
        assert "CASE_CREATED" in actions
        assert "DOCUMENT_UPLOADED" in actions
        assert "ANALYSIS_STARTED" in actions
        assert "ANALYSIS_COMPLETED" in actions

    def test_audit_entries_carry_versions(self, client, case_id):
        trail = client.get(f"/api/cases/{case_id}/audit").json()
        completed = [e for e in trail if e["action"] == "ANALYSIS_COMPLETED"]
        assert completed
        assert completed[0]["rules_version"] == "bank-a-v1"


class TestReportGeneration:
    @pytest.fixture
    def case_id(self, client, conflicting_case_files):
        case = create_case(client)
        upload(client, case["id"], conflicting_case_files)
        analyse(client, case["id"])
        return case["id"]

    def test_report_is_generated_with_both_renderings(self, client, case_id):
        response = client.post(f"/api/cases/{case_id}/reports/generate", json={})
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["status"] == "GENERATED"
        assert report["has_pdf"] is True
        assert report["has_docx"] is True

    def test_report_json_carries_the_findings_and_evidence(self, client, case_id):
        report = client.post(f"/api/cases/{case_id}/reports/generate", json={}).json()
        payload = report["report_json"]

        assert payload["case_summary"]["applicant_name"] == "Ravi Kumar"
        findings = payload["discrepancies"]
        assert findings
        for finding in findings:
            assert finding["id"].startswith("D")
            assert finding["severity"] in {"HIGH", "MEDIUM", "LOW"}
        high = [f for f in findings if f["severity"] == "HIGH"]
        assert high and high[0]["value_1"] and high[0]["value_2"]

    def test_qa_passes_on_a_faithful_report(self, client, case_id):
        report = client.post(f"/api/cases/{case_id}/reports/generate", json={}).json()
        assert report["qa_passed"] is True
        assert report["qa_errors"] == []

    def test_downloads_return_real_documents(self, client, case_id):
        report = client.post(f"/api/cases/{case_id}/reports/generate", json={}).json()

        pdf = client.get(f"/api/reports/{report['id']}/download/pdf")
        assert pdf.status_code == 200
        assert pdf.content[:5] == b"%PDF-"

        docx = client.get(f"/api/reports/{report['id']}/download/docx")
        assert docx.status_code == 200
        assert docx.content[:2] == b"PK"

    def test_a_bank_template_changes_the_report(self, client, conflicting_case_files):
        case = create_case(client, bank_id="bank_b")
        upload(client, case["id"], conflicting_case_files)
        analyse(client, case["id"])

        report = client.post(f"/api/cases/{case['id']}/reports/generate", json={}).json()
        assert report["template_id"] == "bank_b"
        assert "Bank B" in report["report_json"]["title"]
        # bank_b declares a section with no builtin producer, filled by the
        # mapping agent and filtered to its declared keys.
        assert set(report["report_json"]["reviewer_declaration"]) == {
            "reviewed_by", "review_date", "decision", "notes"
        }

    def test_the_default_template_is_used_without_bank_configuration(
        self, client, conflicting_case_files
    ):
        case = create_case(client, bank_id="default")
        upload(client, case["id"], conflicting_case_files)
        analyse(client, case["id"])

        report = client.post(f"/api/cases/{case['id']}/reports/generate", json={}).json()
        assert report["template_id"] == "default"
        assert report["status"] == "GENERATED"


class TestFailureHandling:
    def test_a_corrupt_file_does_not_fail_the_case(self, client, consistent_case_files, tmp_path):
        broken = tmp_path / "broken.pdf"
        broken.write_bytes(b"%PDF-1.4 truncated and unreadable")

        case = create_case(client)
        upload(client, case["id"], {**consistent_case_files, "broken": broken})
        analyse(client, case["id"])

        analysis = client.get(f"/api/cases/{case['id']}/analysis").json()
        documents = {d["filename"]: d for d in analysis["documents"]}
        assert documents["broken.pdf"]["error_code"] == "CORRUPTED_FILE"
        assert documents["Aadhaar.pdf"]["status"] == "EXTRACTED"

    def test_analysis_without_documents_is_refused(self, client):
        case = create_case(client)
        response = client.post(f"/api/cases/{case['id']}/analyze")
        assert response.status_code == 400

    def test_reading_an_unanalysed_case_is_a_conflict_not_an_empty_result(
        self, client, consistent_case_files
    ):
        case = create_case(client)
        upload(client, case["id"], consistent_case_files)
        response = client.get(f"/api/cases/{case['id']}/analysis")
        assert response.status_code == 409
