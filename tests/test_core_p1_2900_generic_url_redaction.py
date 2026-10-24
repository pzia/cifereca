"""P1 #2900: a generic diagnostic never publishes complete URL-like strings."""
from __future__ import annotations

from cifereca.diagnostics import Diagnostics, redact_text


def test_generic_diagnostic_masks_full_urls_even_without_queries():
    private = "PRIVATE_ACCOUNT_72"
    link = "https://bibliotheques.paris.fr/notice/" + private
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
    snap = d.snapshot()
    assert len(snap) == 1
    row = snap[0]
    assert "<redacted-url>" in row["operation"]
    # The legacy redactor may already have replaced a private path suffix;
    # the generic URL scrub masks the remaining URL without undoing it.
    assert row["detail"]["site"].startswith("<redacted-url>")
    assert row["detail"]["attempts"] == 1
    assert row["detail"]["available"] is True
    support = d.text("test")
    assert "<redacted-url>" in support
    for secret in (private, "PRIVATE_BOOK_LINK", link, deep):
        assert secret not in repr(snap)
        assert secret not in support
    assert "https://" not in repr(snap)
    assert "https://" not in support


def test_generic_scrub_does_not_change_public_url_helper_or_verified_sources():
    public = "https://www.gutenberg.org/ebooks/12345"
    assert redact_text(public) == public
    d = Diagnostics(level="normal")
    d.add("PLUGIN", "public site", detail={"homepage": public})
    assert d.snapshot()[0]["detail"]["homepage"] == "<redacted-url>"
    assert "provider_id" not in d.snapshot()[0]["detail"]


def test_generic_diagnostic_does_not_invoke_user_stringify_hooks():
    class Hostile:
        def __str__(self):
            raise AssertionError("PRIVATE_SECRET")

        def __repr__(self):
            raise AssertionError("PRIVATE_SECRET")

    d = Diagnostics(level="normal")
    d.add(Hostile(), Hostile(), detail={"url": Hostile(), "nested": [Hostile()]})
    entry = d.snapshot()[0]
    assert entry["category"] == "DIAGNOSTICS"
    assert entry["operation"] == "[opération masquée]"
    assert entry["detail"]["url"] == "<redacted>"
    assert entry["detail"]["nested"] == ["<redacted>"]
