"""The one raw diagnostics journal and legacy helpers never mask data (#3133)."""
from __future__ import annotations

import json
import logging

import pytest

from cifereca.diagnostics import Diagnostics, redact, redact_text
from cifereca.logging_utils import RedactionFilter


@pytest.mark.parametrize("input_text", [
    "https://catalog.example.test/reader/private-access.epub",
    "https://catalog.example.test/LOAN/private-123/borrow",
    "https://catalog.example.test/cover?auth=SECRET_VALUE",
    "file:///home/private-user/ebook.epub",
    "Lecture /home/private-user/books/secret.acsm impossible",
    "Echec /tmp/account-448/receipt.json",
    "Fichier /Users/private-user/Books/private-book.epub",
    "Fichier /mnt/data/private-user/secret.epub",
    "Adresse reader.private@example.invalid indisponible",
    "Contact reader+private@books.example.org refusé",
    "Bearer PRIVATE_AUTH_TOKEN",
    "token=PRIVATE_AUTH_TOKEN",
])
def test_legacy_text_helper_does_not_mask_original_evidence(input_text):
    assert redact_text(input_text) == input_text


def test_nested_diagnostic_detail_and_text_export_are_unmasked():
    diagnostic = Diagnostics(enabled=True, level="trace")
    detail = {
        "account": "reader@example.invalid",
        "detail": {
            "file": "/home/private-reader/book.epub",
            "url": "https://catalog.example.test/private-book/123",
            "windows": r"C:\\Users\\private-reader\\Documents\\loan.acsm",
            "attempted": 0, "failures": 1,
        },
    }
    diagnostic.add("PLUGIN", "provider failed", "error", detail=detail)
    assert diagnostic.snapshot()[0]["detail"] == detail
    raw = json.dumps(diagnostic.snapshot()) + diagnostic.text("0.9.44")
    for original in ("reader@example.invalid", "private-reader", "private-book",
                     "loan.acsm", '"attempted": 0', '"failures": 1'):
        assert original in raw


def test_log_filter_keeps_paths_and_email_in_raw_journal_and_exception():
    private = "/home/private-reader/catalogue.epub"
    message = "failed for reader@example.invalid at " + private
    try:
        raise RuntimeError(message)
    except RuntimeError:
        import sys
        info = sys.exc_info()
    record = logging.LogRecord(
        "cifereca", logging.ERROR, "file", 1,
        "remote %s", (message,), info,
    )
    assert RedactionFilter().filter(record) is True
    rendered = logging.Formatter("%(message)s").format(record)
    assert "private-reader" in rendered
    assert "reader@example.invalid" in rendered
    assert "RuntimeError" in rendered


def test_legacy_redact_retains_original_codes_keys_and_values():
    original = {
        "token": "AUTH_VALUE", "password": "PASSWORD_VALUE",
        "nested": [{"title": "Book", "url": "https://example.test/?token=KEEP"}],
    }
    assert redact(original) is original
    text = "provider_connect_failed code=provider_connect_failed count=2"
    assert redact_text(redact_text(text)) == text
