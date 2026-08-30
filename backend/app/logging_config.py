"""Logging setup.

Two rules, both about what must never appear in a log line: no document
content, and no unmasked identifier. The filter is a backstop for the first
rule and enforcement for the second, since a stack trace can carry a value that
no call site intended to log.
"""

from __future__ import annotations

import logging
import sys

from app.config import settings
from app.utils.text import mask_sensitive


class MaskingFilter(logging.Filter):
    """Redacts identifiers in a formatted message before it is emitted."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - never let logging break the caller
            return True
        masked = mask_sensitive(message)
        if masked != message:
            record.msg = masked
            record.args = ()
        return True


def configure_logging() -> None:
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    )
    handler.addFilter(MaskingFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # These are chatty at DEBUG and say nothing the application cannot.
    for noisy in ("httpx", "httpcore", "urllib3", "botocore", "boto3", "s3transfer"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))
