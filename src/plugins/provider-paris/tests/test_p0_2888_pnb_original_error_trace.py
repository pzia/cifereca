"""#3867: signed PNB borrow exceptions never enter the canonical local log.

No Paris account, HTTP request, actual borrow/PNB or extra log capture is made.
"""
from __future__ import annotations

import logging

import pytest

from cifereca_paris_client import ParisClient


class Diagnostics:
    def __init__(self):
        self.rows = []

    def add(self, *args, **kwargs):
        self.rows.append((args, kwargs))


def test_pnb_borrow_failure_keeps_type_and_closed_code_without_private_trace(caplog):
    diagnostics = Diagnostics()
    client = ParisClient("https://example.test", diagnostics=diagnostics)
    # Value is synthetic, never a real signed token or real user's credentials.
    original_text = "FICTIONAL_PNB_REJECTION_CHECKOUT_PHASE_77"
    original = RuntimeError(original_text)
    try:
        def fail_borrow():
            raise original

        with caplog.at_level(logging.WARNING, logger="cifereca"):
            with pytest.raises(RuntimeError) as raised:
                client._timed("PNB", "borrow digital", fail_borrow)

        assert raised.value is original
        records = [row for row in caplog.records
                   if "TRACE paris pnb-loan failed" in row.getMessage()]
        assert len(records) == 1  # do not invent a second attempted action
        event = records[0]
        assert event.exc_info is None
        assert "fail_borrow" not in caplog.text
        assert original_text not in caplog.text
        assert "RuntimeError" in caplog.text
        # Public diagnostics remain at their previous shape and status.
        assert len(diagnostics.rows) >= 1
        category, operation, status, _duration, detail = diagnostics.rows[-1][0]
        assert (category, operation, status) == ("PNB", "borrow digital", "error")
        assert detail["error"] == "RuntimeError"
        assert detail["code"] == "paris_pnb_operation_failed"
        assert detail["message"] == detail["code"]
        assert original_text not in repr(diagnostics.rows)
    finally:
        client.close()


def test_non_pnb_failure_not_reported_as_a_pnb_borrow_attempt(caplog):
    client = ParisClient("https://example.test", diagnostics=Diagnostics())
    try:
        with caplog.at_level(logging.WARNING, logger="cifereca"):
            with pytest.raises(ValueError, match="synthetic-search-error"):
                client._timed("CATALOG", "search EBOOKS",
                              lambda: (_ for _ in ()).throw(ValueError("synthetic-search-error")))
        assert not any("pnb-loan failed" in record.getMessage()
                       for record in caplog.records)
    finally:
        client.close()
