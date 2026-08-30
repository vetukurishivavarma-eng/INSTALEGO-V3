"""Evaluation cases with expected outcomes.

Each case is a small document set plus what a correct system should conclude
from it. The cases are the six from the brief, extended with the ones that
matter most for false positives: harmless variation that must not be flagged.

Documents are generated rather than committed so a case can vary exactly one
field, which is what makes a discrepancy test mean something.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tests.fixtures.builders import make_docx, make_pdf, make_xlsx


@dataclass
class ExpectedFinding:
    type: str
    severity: str
    # Values that must both appear in the finding's evidence.
    values: tuple[str, ...] = ()


@dataclass
class EvaluationCase:
    case_id: str
    description: str
    bank_id: str = "bank_a"
    # One or more acceptable outcomes. A confirmed HIGH finding escalates to
    # HIGH_RISK, and whether verification confirms it depends on the model, so
    # both it and REVIEW_REQUIRED are correct for a real mismatch. Pinning a
    # single value here would encode a property of the stub, not of the system.
    expected_status: str | tuple[str, ...] = "REVIEW_REQUIRED"

    @property
    def acceptable_statuses(self) -> tuple[str, ...]:
        if isinstance(self.expected_status, str):
            return (self.expected_status,)
        return self.expected_status
    expected_findings: list[ExpectedFinding] = field(default_factory=list)
    # Finding types that must NOT appear: the false-positive controls.
    forbidden_findings: list[str] = field(default_factory=list)
    expected_fields: dict[str, str] = field(default_factory=dict)
    expected_missing: list[str] = field(default_factory=list)
    builder: Any = None

    def build(self, directory: Path) -> dict[str, Path]:
        return self.builder(directory)


# --------------------------------------------------------------------------
# Document builders, one per case
# --------------------------------------------------------------------------
def _identity_pack(directory: Path, *, dob="12/04/1998", pan="ABCDE1234F",
                   name="Ravi Kumar", address="12 MG Road, Bengaluru 560001") -> dict[str, Path]:
    return {
        "aadhaar": make_pdf(
            directory / "Aadhaar.pdf",
            "AADHAAR - UNIQUE IDENTIFICATION AUTHORITY OF INDIA",
            {"Name": name, "Date of Birth": dob, "Gender": "Male",
             "Aadhaar": "2345 6789 0124", "Address": address},
        ),
        "pan": make_pdf(
            directory / "PAN.pdf",
            "PERMANENT ACCOUNT NUMBER - INCOME TAX DEPARTMENT",
            {"Name": name, "PAN": pan, "Date of Birth": dob},
        ),
    }


def _income_pack(directory: Path, *, gross="50000", net="42000",
                 deductions="8000") -> dict[str, Path]:
    return {
        "payslip": make_pdf(
            directory / "SalarySlip.pdf",
            "SALARY SLIP - ACME TECHNOLOGIES",
            {"Employee Name": "Ravi Kumar", "Employer": "Acme Technologies Pvt Ltd",
             "Salary Period": "July 2026", "Gross Salary": gross,
             "Deductions": deductions, "Net Salary": net},
        ),
        "statement": make_xlsx(
            directory / "BankStatement.xlsx",
            {"Summary": [["Bank Name", "State Bank"],
                         ["Account Holder Name", "Ravi Kumar"],
                         ["Account Number", "000123456789"],
                         ["Closing Balance", "125000"]]},
        ),
    }


def _application(directory: Path, **overrides) -> dict[str, Path]:
    fields = {
        "Applicant Name": "Ravi Kumar",
        "Date of Birth": "12/04/1998",
        "Address": "12, M.G. Road, Bengaluru - 560001",
        "PAN": "ABCDE1234F",
        "Loan Amount": "Rs. 5,00,000",
        "Employer": "Acme Technologies Pvt Ltd",
        "Income": "50000",
    }
    fields.update(overrides)
    return {"application": make_docx(directory / "LoanApplication.docx",
                                     "LOAN APPLICATION", fields)}


def case_001(directory: Path) -> dict[str, Path]:
    return {**_identity_pack(directory), **_income_pack(directory),
            **_application(directory)}


def case_002(directory: Path) -> dict[str, Path]:
    return {**_identity_pack(directory), **_income_pack(directory),
            **_application(directory, **{"Date of Birth": "12/04/1997"})}


def case_003(directory: Path) -> dict[str, Path]:
    return {**_identity_pack(directory), **_income_pack(directory),
            **_application(directory, PAN="ABCDE1234G")}


def case_004(directory: Path) -> dict[str, Path]:
    return {**_identity_pack(directory), **_income_pack(directory),
            **_application(directory, **{"Applicant Name": "Ravi K Kumar"})}


def case_005(directory: Path) -> dict[str, Path]:
    # No payslip and no bank statement: two requirements unmet.
    return {**_identity_pack(directory), **_application(directory)}


def case_006(directory: Path) -> dict[str, Path]:
    return {**_identity_pack(directory), **_income_pack(directory),
            **_application(directory, Address="88 Park Street, Kolkata 700016")}


def case_007(directory: Path) -> dict[str, Path]:
    """Formatting noise only. Nothing here is a discrepancy."""
    return {
        **_identity_pack(directory),
        **_income_pack(directory),
        **_application(
            directory,
            **{
                "Applicant Name": "MR. RAVI KUMAR",
                "Date of Birth": "1998-04-12",
                "Address": "12 M.G. Rd., Bengaluru-560001",
                "PAN": "abcde1234f",
                "Loan Amount": "500000.00",
            },
        ),
    }


def case_008(directory: Path) -> dict[str, Path]:
    """The applicant asked for one figure; the sanction letter states another."""
    documents = {**_identity_pack(directory), **_income_pack(directory),
                 **_application(directory, **{"Loan Amount": "Rs. 7,50,000"})}
    documents["sanction"] = make_pdf(
        directory / "SanctionLetter.pdf",
        "LOAN AGREEMENT AND SANCTION LETTER",
        {"Party One": "State Bank", "Party Two": "Ravi Kumar",
         "Agreement Date": "01/08/2026", "Loan Amount": "Rs. 5,00,000",
         "Subject": "Personal loan sanction"},
    )
    return documents


def case_009(directory: Path) -> dict[str, Path]:
    """An expired passport alongside otherwise consistent documents."""
    documents = {**_identity_pack(directory), **_income_pack(directory),
                 **_application(directory)}
    documents["passport"] = make_pdf(
        directory / "Passport.pdf",
        "REPUBLIC OF INDIA PASSPORT",
        {"Name": "Ravi Kumar", "Passport": "P1234567", "Date of Birth": "12/04/1998",
         "Date of Issue": "01/01/2015", "Date of Expiry": "01/01/2025"},
    )
    return documents


CASES: list[EvaluationCase] = [
    EvaluationCase(
        case_id="001",
        description="all documents consistent",
        expected_status="CLEAR",
        expected_findings=[],
        forbidden_findings=["DOB_MISMATCH", "NAME_MISMATCH", "PAN_MISMATCH",
                            "ADDRESS_MISMATCH", "LOAN_AMOUNT_MISMATCH"],
        expected_fields={"name": "Ravi Kumar", "date_of_birth": "12/04/1998",
                         "pan": "ABCDE1234F"},
        builder=case_001,
    ),
    EvaluationCase(
        case_id="002",
        description="date of birth differs between Aadhaar and the application",
        expected_status=("REVIEW_REQUIRED", "HIGH_RISK"),
        expected_findings=[ExpectedFinding("DOB_MISMATCH", "HIGH",
                                           ("12/04/1998", "12/04/1997"))],
        forbidden_findings=["NAME_MISMATCH", "PAN_MISMATCH"],
        builder=case_002,
    ),
    EvaluationCase(
        case_id="003",
        description="PAN differs between the PAN card and the application",
        expected_status=("REVIEW_REQUIRED", "HIGH_RISK"),
        expected_findings=[ExpectedFinding("PAN_MISMATCH", "HIGH",
                                           ("ABCDE1234F", "ABCDE1234G"))],
        forbidden_findings=["DOB_MISMATCH", "NAME_MISMATCH"],
        builder=case_003,
    ),
    EvaluationCase(
        case_id="004",
        description="name written with a middle initial in one document",
        expected_status="CLEAR",
        expected_findings=[],
        forbidden_findings=["NAME_MISMATCH", "DOB_MISMATCH", "PAN_MISMATCH"],
        builder=case_004,
    ),
    EvaluationCase(
        case_id="005",
        description="income proof and bank statement missing",
        expected_status=("REVIEW_REQUIRED", "HIGH_RISK"),
        expected_findings=[ExpectedFinding("MISSING_REQUIRED_DOCUMENT", "HIGH")],
        expected_missing=["INCOME_PROOF", "BANK_STATEMENT"],
        forbidden_findings=["DOB_MISMATCH", "PAN_MISMATCH"],
        builder=case_005,
    ),
    EvaluationCase(
        case_id="006",
        description="address differs materially between documents",
        expected_status=("REVIEW_REQUIRED", "HIGH_RISK"),
        expected_findings=[ExpectedFinding("ADDRESS_MISMATCH", "MEDIUM")],
        forbidden_findings=["DOB_MISMATCH", "PAN_MISMATCH", "NAME_MISMATCH"],
        builder=case_006,
    ),
    EvaluationCase(
        case_id="007",
        description="formatting variation only: case, punctuation, date format",
        expected_status="CLEAR",
        expected_findings=[],
        forbidden_findings=["DOB_MISMATCH", "NAME_MISMATCH", "PAN_MISMATCH",
                            "ADDRESS_MISMATCH", "LOAN_AMOUNT_MISMATCH"],
        builder=case_007,
    ),
    EvaluationCase(
        case_id="008",
        description="loan amount differs between the application and the sanction letter",
        expected_status=("REVIEW_REQUIRED", "HIGH_RISK"),
        expected_findings=[ExpectedFinding("LOAN_AMOUNT_MISMATCH", "HIGH",
                                           ("Rs. 7,50,000", "Rs. 5,00,000"))],
        forbidden_findings=["DOB_MISMATCH", "NAME_MISMATCH"],
        builder=case_008,
    ),
    EvaluationCase(
        case_id="009",
        description="an expired passport among consistent documents",
        expected_status=("REVIEW_REQUIRED", "HIGH_RISK"),
        expected_findings=[ExpectedFinding("EXPIRED_DOCUMENT", "HIGH")],
        forbidden_findings=["DOB_MISMATCH", "NAME_MISMATCH", "PAN_MISMATCH"],
        builder=case_009,
    ),
]
