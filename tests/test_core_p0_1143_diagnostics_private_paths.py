"""P0 #1143: last-resort free-text diagnostic redaction covers private paths.

The origin of an HTTPS link is retained for backwards compatibility; path,
query, fragment, embedded credentials, local filesystem paths and emails are
not. Provider-specific consumers should prefer closed error codes.
"""
from __future__ import annotations

import json
import logging

import pytest

from cifereca.diagnostics import Diagnostics, redact, redact_text
from cifereca.logging_utils import RedactionFilter


@pytest.mark.parametrize(("input_text", "secret"), [
    ("https://catalog.example.test/reader/private-access.epub", "private-access.epub"),
    ("https://catalog.example.test/LOAN/private-123/borrow", "private-123"),
    ("file:///home/private-user/ebook.epub", "private-user"),
    ("Lecture /home/private-user/books/secret.acsm impossible", "private-user"),
    ("Echec /tmp/account-448/receipt.json", "account-448"),
    ("Fichier /Users/private-user/Books/private-book.epub", "private-user"),
    ("Fichier /mnt/data/private-user/secret.epub", "private-user"),
    ("Dossier C:\\Users\\private-user\\Books\\secret.epub", "private-user"),
    ("Dossier D:/Users/private-user/Books/secret.epub", "private-user"),
    ("Adresse reader.private@example.invalid indisponible", "reader.private@example.invalid"),
    ("Contact reader+private@books.example.org refusé", "reader+private@books.example.org"),
    ("Bearer PRIVATE_AUTH_TOKEN", "PRIVATE_AUTH_TOKEN"),
    ("token=PRIVATE_AUTH_TOKEN", "PRIVATE_AUTH_TOKEN"),
])
def test_private_information_is_never_forwarded_from_diagnostic_text(
    input_text, secret,
):
    output = redact_text(input_text)
    assert secret not in output
    assert len(output) < len(input_text) + 30


def test_unsigned_catalog_url_path_is_redacted_but_public_origin_remains():
    assert redact_text("https://catalog.example.test/public/reader/123") == (
        "https://catalog.example.test/<redacted>"
    )
    assert redact_text("https://catalog.example.test/") == (
        "https://catalog.example.test/"
    )
    # Public non-sensitive paths remain visible by the historical contract.
    assert redact_text("https://catalog.example.test/book") == (
        "https://catalog.example.test/book"
    )
    assert redact_text("https://catalog.example.test/cover?auth=SECRET_VALUE") == (
        "https://catalog.example.test/<redacted>"
    )


def test_nested_detail_and_export_redact_without_altering_structured_counters():
    diagnostic = Diagnostics(enabled=True, level="trace")
    diagnostic.add("PLUGIN", "provider failed", "error", detail={
        "account": "reader@example.invalid",
        "detail": {
            "file": "/home/private-reader/book.epub",
            "url": "https://catalog.example.test/private-book/123",
            "windows": "C:\\Users\\private-reader\\Documents\\loan.acsm",
            "attempted": 0, "failures": 1,
        },
    })
    raw = json.dumps(diagnostic.snapshot()) + diagnostic.text("0.9.44")
    assert "reader@example.invalid" not in raw
    assert "private-reader" not in raw
    assert "private-book" not in raw
    assert "loan.acsm" not in raw
    assert '"attempted": 0' in raw
    assert '"failures": 1' in raw


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
    rendered = record.getMessage() + str(record.exc_text)
    assert "private-reader" in rendered
    assert "reader@example.invalid" in rendered
    assert "RuntimeError" in rendered


def test_diagnostic_redaction_remains_idempotent_for_public_codes():
    text = "provider_connect_failed code=provider_connect_failed count=2"
    assert redact_text(text) == text
    assert redact_text(redact_text(text)) == text
    assert redact({"code": "provider_connect_failed", "attempted": 0}) == {
        "code": "provider_connect_failed", "attempted": 0,
    }
