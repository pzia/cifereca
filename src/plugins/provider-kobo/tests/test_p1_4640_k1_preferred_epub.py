"""#4640 K1: explicit owner's EPUB-over-PDF decision, fully scoped.

Synthetic provider descriptor and mocked bytes only; no Vault or content GET
beyond the fake local client. Never choose between two admissible EPUBs.
"""
from copy import deepcopy

import httpx
import pytest

from cifereca.plugin_api import HTTPX_TRANSPORT, ProviderCapabilityError, ProviderError
from cifereca_kobo_delivery_offer import exact_kobo_offer_id
from tests.test_p1_3252_kobo_selected_delivery_offer import descriptor, setup
from tests.test_p1_3252_kobo_selected_candidate import (
    admitted, execute, epub_bytes, fake_public_client,
)

SCOPE = dict(
    identity_id="identity-A", credential_id="synthetic-card",
    account_id="alice", product_id="book-1",
)


def _two_formats(order=("epub", "pdf")):
    source = descriptor()
    first = deepcopy(source["acquisition"]["candidates"][0])
    rows = []
    for fmt in order:
        row = deepcopy(first)
        row.update(
            media_format=fmt, source_format=fmt,
            url=f"https://cdn.kobo.com/private/book-1.{fmt}?token=PRIVATE-{fmt}",
        )
        rows.append(row)
    source["acquisition"]["candidates"] = rows
    return source


@pytest.mark.parametrize("order", [("epub", "pdf"), ("pdf", "epub")])
def test_k1_unique_admissible_epub_wins_both_orders(order):
    payload = _two_formats(order)
    expected = exact_kobo_offer_id(
        descriptor(candidate=next(row for row in payload["acquisition"]["candidates"]
                                  if row["media_format"] == "epub")), **SCOPE,
    )
    diagnostic = {}
    assert exact_kobo_offer_id(payload, **SCOPE, diagnostic=diagnostic) == expected
    assert diagnostic["candidate_selection"] == "unique_epub_preferred"
    assert diagnostic["candidate_shape_counts"] == {
        "content_access_book:epub:epub:http_plain": 1,
        "content_access_book:pdf:pdf:http_plain": 1,
    }
    assert "PRIVATE" not in str(diagnostic)
    assert "https://" not in str(diagnostic)


@pytest.mark.parametrize("order", [("epub", "pdf"), ("pdf", "epub")])
def test_k1_real_private_selection_stages_only_epub(order, tmp_path, monkeypatch):
    session, vault, _broker, account, posts = setup()
    source = _two_formats(order)
    vault.acquisition = lambda product, **kwargs: (
        posts.append((product, kwargs)) or deepcopy(source)
    )
    calls = []
    expected = epub_bytes()
    fake_public_client(
        monkeypatch,
        lambda request: (
            calls.append((request.method, str(request.url))) or
            httpx.Response(200, content=expected, headers={
                "content-type": "application/epub+zip",
            })
        ),
        [],
    )
    try:
        offer = admitted(session, account)
        assert offer.startswith("kobo.descriptor:")
        assert calls == []
        path, reference = execute(session, account, offer, tmp_path)
        assert path.suffix == ".epub" and path.read_bytes() == expected
        assert reference == ""
        assert calls == [
            ("GET", "https://cdn.kobo.com/private/book-1.epub?token=PRIVATE-epub"),
        ]
        assert len(posts) == 2
    finally:
        session.close()


@pytest.mark.parametrize("order", [("epub", "epub", "pdf"), ("pdf", "epub", "epub")])
def test_k1_two_epubs_remain_ambiguous_even_with_pdf(order):
    payload = _two_formats(order)
    urls = set()
    for index, row in enumerate(payload["acquisition"]["candidates"]):
        row["url"] = row["url"].replace("?token=", f"?variant={index}&token=")
        assert row["url"] not in urls
        urls.add(row["url"])
    diagnostic = {}
    assert exact_kobo_offer_id(payload, **SCOPE, diagnostic=diagnostic) == ""
    assert diagnostic["candidate_selection"] == "multiple_eligible_candidates"
    assert diagnostic["eligible_candidate_count"] >= 2


def test_k1_invalid_epub_does_not_override_valid_pdf():
    payload = _two_formats()
    epub = payload["acquisition"]["candidates"][0]
    epub["method"] = "POST"
    pdf = payload["acquisition"]["candidates"][1]
    expected = exact_kobo_offer_id(descriptor(candidate=pdf), **SCOPE)
    diagnostic = {}
    assert exact_kobo_offer_id(payload, **SCOPE, diagnostic=diagnostic) == expected
    assert "unique_epub_preferred" not in diagnostic.values()


@pytest.mark.parametrize("fault", [
    ("identity_id", "another-identity"),
    ("credential_id", "other-credential"),
    ("account_id", "other-account"),
    ("product_id", "other-product"),
])
def test_k1_foreign_sibling_taints_complete_offer(fault):
    payload = _two_formats()
    key, value = fault
    payload["acquisition"]["candidates"][0][key] = value
    diag = {}
    assert exact_kobo_offer_id(payload, **SCOPE, diagnostic=diag) == ""
    assert diag == {"qualification_reason": "candidate_scope_conflict"}


def test_k1_fresh_pdf_only_revokes_prior_epub_offer_without_get(tmp_path, monkeypatch):
    session, vault, _, account, posts = setup()
    source = _two_formats()
    vault.acquisition = lambda product, **kwargs: (
        posts.append((product, kwargs)) or deepcopy(source)
    )
    calls = []
    monkeypatch.setattr(
        HTTPX_TRANSPORT, "attested_https_download",
        lambda **kwargs: calls.append(kwargs) or pytest.fail("No content GET"),
    )
    try:
        offer = admitted(session, account)
        source["acquisition"]["candidates"] = [
            row for row in source["acquisition"]["candidates"]
            if row["media_format"] == "pdf"
        ]
        with pytest.raises((ProviderCapabilityError, ProviderError)):
            execute(session, account, offer, tmp_path)
        assert calls == []
        assert list(tmp_path.iterdir()) == []
        assert len(posts) == 2
    finally:
        session.close()
