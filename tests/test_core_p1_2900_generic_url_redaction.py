"""#3133: generic diagnostics preserve raw JSON-compatible values."""
from __future__ import annotations

from cifereca.diagnostics import Diagnostics, redact_text


def test_generic_diagnostic_preserves_urls_and_nested_values():
    link = "https://bibliotheques.paris.fr/notice/PRIVATE_ACCOUNT_72"
    deep = "https://example.test/books/PRIVATE_BOOK_LINK"
    d = Diagnostics(level="normal")
    d.add(
        "PNB " + link, "open " + link, status="failed",
        detail={
            "site": link,
            "description": "visit " + deep + " and " + link,
            "nested": [{"external_site": deep}, "download " + link],
            "attempts": 1, "available": True,
        },
    )
    row, = d.snapshot()
    assert row["category"] == "PNB " + link
    assert row["operation"] == "open " + link
    assert row["detail"]["site"] == link
    assert row["detail"]["description"] == "visit " + deep + " and " + link
    assert row["detail"]["nested"] == [{"external_site": deep}, "download " + link]
    assert row["detail"]["attempts"] == 1
    assert row["detail"]["available"] is True
    support = d.text("test")
    assert link in support and deep in support


def test_generic_collector_does_not_apply_public_redaction_helper():
    public = "https://www.gutenberg.org/ebooks/12345"
    assert redact_text(public) == public
    d = Diagnostics(level="normal")
    d.add("PLUGIN", "public site", detail={"homepage": public})
    assert d.snapshot()[0]["detail"]["homepage"] == public


def test_generic_diagnostic_does_not_invoke_user_stringify_hooks():
    class Hostile:
        def __str__(self):
            raise AssertionError("no string conversion")

        def __repr__(self):
            raise AssertionError("no repr conversion")

    d = Diagnostics(level="normal")
    d.add(Hostile(), Hostile(), detail={"url": Hostile(), "nested": [Hostile()]})
    entry = d.snapshot()[0]
    assert entry["category"] == "DIAGNOSTICS"
    assert entry["operation"] == "[opération masquée]"
    assert entry["detail"]["url"] is None
    assert entry["detail"]["nested"] == [None]
