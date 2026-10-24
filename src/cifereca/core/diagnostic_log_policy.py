"""Closed Core observations retained independently of verbose free-text TRACE."""
from __future__ import annotations

import re

_TASK_RESULT = re.compile(
    r"TRACE task correlation task_ref=[0-9a-f]{12} status=(?:success|failed|cancelled)\Z"
)


def is_public_core_observation(logger: str, message: str) -> bool:
    """Validate the whole original body, never a redacted/truncated prefix.

    These are the same canonical schemas consumed by the public event feed.
    This does not enable arbitrary plugin TRACE, infer provider provenance,
    inspect files, query providers or serialize caller-supplied objects.
    """
    if type(logger) is not str or logger != "cifereca":
        return False
    # Closed reconciliation observations can exceed the old TRACE-only cap.
    # Each producer-specific parser below enforces its own smaller bound.
    if type(message) is not str or len(message) > 4096:
        return False
    if _TASK_RESULT.fullmatch(message):
        return True
    from .public_provider_diagnostics import (
        public_connection_log_fields,
        public_connection_success_log_fields,
    )
    from .diagnostic_loan_download import public_log_fields as loan_download
    from .diagnostic_reconciliation import public_reconciliation_log_fields as audit
    from .diagnostic_source_sync import public_log_fields as source_sync
    from .diagnostic_adept import public_log_fields as adept
    from .diagnostic_kobo_projection import public_kobo_backup_log
    from .numilog_startup_diagnostics import public_log_fields as numilog_startup
    return bool(
        public_connection_log_fields(message)
        or public_connection_success_log_fields(message)
        or loan_download(message)
        or audit(message)
        or source_sync(message)
        or adept(message)
        or public_kobo_backup_log(message)
        or numilog_startup(message)
    )
