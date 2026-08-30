"""Integration-suite guards.

These tests assert behaviour, not model quality: each one re-runs the pipeline
so it can inspect a fresh case, which is free against the stub and wasteful
against a metered endpoint. Eight tests on the same three documents would spend
roughly seventy model calls to re-derive the same analysis.

Live runs belong to the evaluation suite, whose fixture runs each case once.
"""

from __future__ import annotations

import os

import pytest

LIVE = os.environ.get("LDAI_LIVE_LLM") == "1"


def pytest_collection_modifyitems(config, items):  # noqa: ARG001
    if not LIVE:
        return
    skip = pytest.mark.skip(
        reason=(
            "integration tests re-run the pipeline per test; against a live "
            "endpoint use the evaluation suite instead (make eval-live)"
        )
    )
    for item in items:
        item.add_marker(skip)
