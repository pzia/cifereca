"""#2900 — local log last-resort privacy, without losing source/book context."""
from __future__ import annotations

import logging
import sys

import pytest

from cifereca.logging_utils import RedactionFilter, _redact_log_identifiers


@pytest.mark.parametrize("message", [
    "loan_id=PERSON_PRIVATE provider_id=paris book_id=9782000000000 site=bibliotheques.paris.fr",
    "account_key: PERSON_PRIVATE provider_id=kobo",
    "account_id='PERSON_PRIVATE' stage=download",
    '{"credential_id": "PERSON_PRIVATE", "provider_id": "paris"}',
    "{'identity_id': 'PERSON_PRIVATE with spaces', 'book_id': '9782000000000'}",
    "reader_id=PERSON_PRIVATE uid=PERSON_PRIVATE user_id=PERSON_PRIVATE",
    "username=PERSON_PRIVATE profile_id=PERSON_PRIVATE source_account_id=PERSON_PRIVATE",
])
def test_log_identifier_redaction_preserves_public_trace_context(message):
    result = _redact_log_identifiers(message)
    assert "PERSON_PRIVATE" not in result
    if "provider_id=paris" in message:
        assert "provider_id=paris" in result
    if "book_id=9782000000000" in message:
        assert "book_id=9782000000000" in result


def test_private_identity_and_email_cannot_escape_logging_filter():
    record = logging.LogRecord(
        name="cifereca", level=logging.WARNING,
        pathname=__file__, lineno=1,
        msg=("sync provider_id=paris site=bibliotheques.paris.fr book_id=9782000000000 "
             "loan_id=loan-PRIVATE account_key=reader-PRIVATE "
             "email=reader@example.test password=PASSWORD_PRIVATE uid_present=true"),
        args=(), exc_info=None,
    )
    assert RedactionFilter().filter(record) is True
    output = record.getMessage()
    assert "provider_id=paris" in output
    assert "site=bibliotheques.paris.fr" in output
    assert "book_id=9782000000000" in output
    assert "uid_present=true" in output
    for forbidden in ("loan-PRIVATE", "reader-PRIVATE", "reader@example.test", "PASSWORD_PRIVATE"):
        assert forbidden not in output


def test_private_identity_is_removed_from_exception_traceback():
    try:
        raise RuntimeError("loan_id=loan-PRIVATE account_id='account-PRIVATE'")
    except RuntimeError:
        error = sys.exc_info()
    record = logging.LogRecord(
        name="cifereca", level=logging.ERROR,
        pathname=__file__, lineno=1, msg="provider_id=paris failed",
        args=(), exc_info=error,
    )
    assert RedactionFilter().filter(record) is True
    assert "loan-PRIVATE" not in record.exc_text
    assert "account-PRIVATE" not in record.exc_text
    assert "loan_id=<redacted>" in record.exc_text
