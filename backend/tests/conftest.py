"""Test configuration.

The environment is set before anything from ``app`` is imported, because
settings are read once at import time. Tests get their own SQLite database and
their own storage root, and the mock model client, so the suite runs with no
Postgres, no Redis and no GPU.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

TEST_ROOT = Path(tempfile.gettempdir()) / "ldai-tests"
TEST_ROOT.mkdir(parents=True, exist_ok=True)

os.environ.update(
    {
        "ENVIRONMENT": "test",
        "LOG_LEVEL": "WARNING",
        "DATABASE_URL": f"sqlite+pysqlite:///{(TEST_ROOT / 'test.db').as_posix()}",
        "STORAGE_BACKEND": "local",
        "STORAGE_LOCAL_ROOT": str(TEST_ROOT / "documents"),
        "TASK_QUEUE_BACKEND": "inline",
        "AUTH_ENABLED": "false",
        "DEFAULT_BANK_ID": "bank_a",
    }
)

# LDAI_LIVE_LLM=1 runs the suite against the configured endpoint instead of the
# stub. This is how the evaluation numbers get to mean something: the same
# cases, the same expectations, a real model doing the extraction.
LIVE_LLM = os.environ.get("LDAI_LIVE_LLM") == "1"
if not LIVE_LLM:
    os.environ["LLM_USE_MOCK"] = "true"
else:
    os.environ["LLM_USE_MOCK"] = "false"
    # LLM_BASE_URL, LLM_MODEL and LLM_API_KEY are deliberately NOT defaulted
    # here. Environment variables outrank .env in settings, so a default set
    # in this file would silently shadow the endpoint the operator configured
    # and point a "live" run at somewhere that is not listening.


@pytest.fixture(scope="session", autouse=True)
def _database() -> None:
    """One schema for the session; each test cleans up after itself."""
    from app.db import engine, init_db
    import app.models  # noqa: F401  (registers the mappers)

    database = TEST_ROOT / "test.db"
    if database.exists():
        database.unlink()

    init_db()
    yield
    engine.dispose()
    shutil.rmtree(TEST_ROOT / "documents", ignore_errors=True)


@pytest.fixture
def db():
    """A session that rolls nothing back: the pipeline commits as it goes."""
    from app.db import SessionLocal

    session = SessionLocal()
    try:
        yield session
        session.commit()
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _clean_tables():
    """Truncate between tests so case references and counts stay predictable."""
    yield
    from app.db import SessionLocal
    from app.models import (
        ApplicantProfile,
        AuditLog,
        Case,
        Discrepancy,
        Document,
        DocumentPage,
        Evidence,
        Extraction,
        FieldValue,
        Report,
        ValidationResult,
    )

    session = SessionLocal()
    try:
        for model in (
            Evidence, Discrepancy, ValidationResult, FieldValue, Extraction,
            DocumentPage, Document, ApplicantProfile, Report, AuditLog, Case,
        ):
            session.query(model).delete()
        session.commit()
    finally:
        session.close()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def documents_dir(tmp_path) -> Path:
    return tmp_path


@pytest.fixture
def consistent_case_files(tmp_path) -> dict[str, Path]:
    """An applicant whose documents all agree with each other."""
    from tests.fixtures.builders import make_docx, make_pdf, make_xlsx

    return {
        "aadhaar": make_pdf(
            tmp_path / "Aadhaar.pdf",
            "AADHAAR - UNIQUE IDENTIFICATION AUTHORITY OF INDIA",
            {
                "Name": "Ravi Kumar",
                "Date of Birth": "12/04/1998",
                "Gender": "Male",
                "Aadhaar": "2345 6789 0124",
                "Address": "12 MG Road, Bengaluru 560001",
            },
        ),
        "pan": make_pdf(
            tmp_path / "PAN.pdf",
            "PERMANENT ACCOUNT NUMBER - INCOME TAX DEPARTMENT",
            {
                "Name": "Ravi Kumar",
                "PAN": "ABCDE1234F",
                "Date of Birth": "12/04/1998",
                "Father Name": "Suresh Kumar",
            },
        ),
        "application": make_docx(
            tmp_path / "LoanApplication.docx",
            "LOAN APPLICATION",
            {
                "Applicant Name": "Ravi Kumar",
                "Date of Birth": "12/04/1998",
                "Address": "12, M.G. Road, Bengaluru - 560001",
                "Phone": "+91 98765 43210",
                "Email": "ravi.kumar@example.com",
                "PAN": "ABCDE1234F",
                "Loan Amount": "Rs. 5,00,000",
                "Employer": "Acme Technologies Pvt Ltd",
                "Income": "50000",
            },
        ),
        "payslip": make_pdf(
            tmp_path / "SalarySlip.pdf",
            "SALARY SLIP - ACME TECHNOLOGIES",
            {
                "Employee Name": "Ravi Kumar",
                "Employer": "Acme Technologies Pvt Ltd",
                "Designation": "Senior Engineer",
                "Salary Period": "July 2026",
                "Gross Salary": "50000",
                "Deductions": "8000",
                "Net Salary": "42000",
            },
        ),
        "statement": make_xlsx(
            tmp_path / "BankStatement.xlsx",
            {
                "Summary": [
                    ["Bank Name", "State Bank"],
                    ["Account Holder Name", "Ravi Kumar"],
                    ["Account Number", "000123456789"],
                    ["Statement Period", "01/07/2026 to 31/07/2026"],
                    ["Closing Balance", "125000"],
                ],
                "Transactions": [
                    ["Date", "Description", "Amount"],
                    ["05/07/2026", "Salary credit", "42000"],
                ],
            },
        ),
    }


@pytest.fixture
def conflicting_case_files(tmp_path) -> dict[str, Path]:
    """The same applicant, but the application disagrees on DOB and PAN."""
    from tests.fixtures.builders import make_docx, make_pdf

    return {
        "aadhaar": make_pdf(
            tmp_path / "Aadhaar.pdf",
            "AADHAAR - UNIQUE IDENTIFICATION AUTHORITY OF INDIA",
            {
                "Name": "Ravi Kumar",
                "Date of Birth": "12/04/1998",
                "Aadhaar": "2345 6789 0124",
                "Address": "12 MG Road, Bengaluru 560001",
            },
        ),
        "pan": make_pdf(
            tmp_path / "PAN.pdf",
            "PERMANENT ACCOUNT NUMBER - INCOME TAX DEPARTMENT",
            {
                "Name": "Ravi Kumar",
                "PAN": "ABCDE1234F",
                "Date of Birth": "12/04/1998",
            },
        ),
        "application": make_docx(
            tmp_path / "LoanApplication.docx",
            "LOAN APPLICATION",
            {
                "Applicant Name": "Ravi K Kumar",
                "Date of Birth": "12/04/1997",
                "Address": "88 Park Street, Kolkata 700016",
                "PAN": "ABCDE1234G",
                "Loan Amount": "Rs. 7,50,000",
            },
        ),
    }
