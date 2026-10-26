"""#3133: compatibility helpers preserve paths, URLs and credentials outside GTK."""
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
def test_legacy_text_helper_retains_original_information(
    input_text, secret,
):
    output = redact_text(input_text)
    assert output == input_text
    assert secret in output


def test_catalog_url_and_signed_query_are_kept():
    for value in (
        "https://catalog.example.test/public/reader/123",
        "https://catalog.example.test/",
        "https://catalog.example.test/book",
        "https://catalog.example.test/cover?auth=SECRET_VALUE",
    ):
        assert redact_text(value) == value


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
    # The canonical journal and both legacy views now retain original data.
    for value in ("reader@example.invalid", "private-reader", "private-book", "loan.acsm"):
        assert value in raw
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
    rendered = logging.Formatter("%(message)s").format(record)
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
