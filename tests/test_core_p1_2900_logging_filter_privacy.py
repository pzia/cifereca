"""#3132: journal Core et miroir console fidèles; le masquage est exclusivement IHM."""
from __future__ import annotations

import logging
import sys

import pytest

from cifereca import logging_utils


@pytest.mark.parametrize("message", [
    "loan_id=PERSON_PRIVATE provider_id=paris book_id=9782000000000 site=bibliotheques.paris.fr",
    "account_key: PERSON_PRIVATE provider_id=kobo",
    "account_id='PERSON_PRIVATE' stage=download",
    '{"credential_id": "PERSON_PRIVATE", "provider_id": "paris"}',
    "reader_id=PERSON_PRIVATE uid=PERSON_PRIVATE user_id=PERSON_PRIVATE",
    "username=PERSON_PRIVATE profile_id=PERSON_PRIVATE source_account_id=PERSON_PRIVATE",
    "https://reader.example.invalid/LOAN/abc.acsm?token=PERSON_PRIVATE",
    "password=PERSON_PRIVATE cookie=PERSON_PRIVATE email=reader@example.test",
])
def test_journal_preserves_original_context(monkeypatch, message):
    monkeypatch.setattr(logging_utils, "_diagnostic_level", "normal")
    record = logging.LogRecord("cifereca", logging.WARNING, __file__, 1, "%s", (message,), None)
    original = (record.msg, record.args)
    assert logging_utils.RedactionFilter().filter(record) is True
    assert record.getMessage() == message
    assert (record.msg, record.args) == original
    # A second handler (console) must receive exactly the same record.
    assert logging_utils.RedactionFilter().filter(record) is True
    assert record.getMessage() == message


def test_traceback_and_stack_are_not_sanitized_by_journal_filter(monkeypatch):
    monkeypatch.setattr(logging_utils, "_diagnostic_level", "normal")
    try:
        raise RuntimeError("loan_id=loan-PRIVATE account_id='account-PRIVATE'")
    except RuntimeError:
        error = sys.exc_info()
    record = logging.LogRecord("cifereca", logging.ERROR, __file__, 1,
                               "provider_id=paris failed", (), error)
    record.exc_text = "PRIVATE cached traceback"
    record.stack_info = "/home/private-user/trace.py password=PRIVATE"
    assert logging_utils.RedactionFilter().filter(record)
    assert record.exc_info is error
    assert record.exc_text == "PRIVATE cached traceback"
    assert "private-user" in record.stack_info


def test_filter_keeps_original_values_but_obeys_trace_level(monkeypatch):
    record = logging.LogRecord("cifereca", logging.INFO, __file__, 1,
                               "TRACE arbitrary token=SECRET", (), None)
    monkeypatch.setattr(logging_utils, "_diagnostic_level", "normal")
    assert not logging_utils.RedactionFilter().filter(record)
    monkeypatch.setattr(logging_utils, "_diagnostic_level", "trace")
    assert logging_utils.RedactionFilter().filter(record)
    assert record.getMessage() == "TRACE arbitrary token=SECRET"
