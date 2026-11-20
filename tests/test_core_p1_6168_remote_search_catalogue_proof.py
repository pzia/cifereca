"""P1 #6168: supplier's OWN SearchResult is the only catalogue image proof.

Public catalogue image evidence never conveys title-based equivalence,
purchase, loan or access to another owner's physical EPUB.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from cifereca.core.search_cover_provenance import (
    exact_search_cover_scope, project_exact_search_catalogue_cover,
)
from cifereca.gui.search_cover_bridge import _scope
from cifereca.models import SearchResult


URL = "https://covers.example.org/book.png"


def result(**kwargs):
    original = SearchResult(
        id="provider-ui-id", record_id="item-123", docbase="catalogue",
        medium="digital", title="Une édition", author="Auteure",
        type="book", format="epub", publisher=None, published_at=None,
        description=None, thumbnail_url=URL, friendly_url=None,
        scenario="catalogue", source_provider="books", source_credential="account-A",
        source_account_id="borrower-A", source_collection="library",
        revision_id="rev-1",
    )
    return replace(original, **kwargs)


def test_core_scope_is_identical_to_legacy_exact_search_n2_scope():
    item = result()
    scope = exact_search_cover_scope(item, "alice")
    assert scope is not None and scope == _scope(item, "alice")
    assert scope.kind == "catalogue"
    assert scope.identity_id == "alice"
    assert scope.provider_id == "books"
    assert scope.credential_id == "account-A"
    assert scope.account_id == "borrower-A"
    assert scope.source_key.startswith("search-exact:")
    assert project_exact_search_catalogue_cover(item, identity_id="alice").scope == scope


@pytest.mark.parametrize("change", [
    {"record_id": "another-edition"},
    {"docbase": "another-base"},
    {"medium": "physical"},
    {"medium": "unknown"},
    {"source_provider": "another-provider"},
    {"source_credential": "other-credential"},
    {"source_account_id": "borrower-B"},
    {"source_collection": "public_catalog"},
    {"revision_id": "rev-2"},
])
def test_source_boundaries_must_never_alias(change):
    original = exact_search_cover_scope(result(), "alice")
    changed = exact_search_cover_scope(result(**change), "alice")
    assert changed is not None and original is not None and changed != original


def test_profile_separation_is_documentary_not_physical():
    item = result()
    alice = project_exact_search_catalogue_cover(item, identity_id="alice")
    bob = project_exact_search_catalogue_cover(item, identity_id="bob")
    assert alice is not None and bob is not None
    assert alice.scope != bob.scope
    # Same supplier image bytes can be reused by the same N3 cache on desktop.
    assert alice.catalogue.url == bob.catalogue.url == URL
    assert alice.catalogue.tier == bob.catalogue.tier == "catalog"
    assert alice.catalogue.source == bob.catalogue.source == "search-result"


@pytest.mark.parametrize("alter", [
    {"source_provider": ""},
    {"source_credential": None},
    {"record_id": None},
    {"docbase": ""},
    {"medium": None},
    {"medium": "unsupported"},
    {"source_account_id": "\x00"},
    {"source_collection": "\n"},
    {"revision_id": "r" * 513},
])
def test_bad_provider_coordinates_never_mint_source(alter):
    assert exact_search_cover_scope(result(**alter), "alice") is None
    assert project_exact_search_catalogue_cover(
        result(**alter), identity_id="alice",
    ) is None


@pytest.mark.parametrize("url", [
    None, "file:///home/user/private.jpg", "http://127.0.0.1/a.png",
    "https://covers.example.org/private?token=secret",
    "https://account:password@covers.example.org/photo.png",
    "javascript:alert(1)", "https://localhost/photo.jpg",
])
def test_non_public_or_signed_cover_cannot_enter_shared_n2(url):
    assert project_exact_search_catalogue_cover(
        result(thumbnail_url=url), identity_id="alice",
    ) is None


def test_stale_known_catalogue_is_not_fresh_supplier_evidence():
    assert project_exact_search_catalogue_cover(
        result(metadata={"search_provenance": "known_catalog_stale"}),
        identity_id="alice",
    ) is None


def test_title_is_neither_part_of_source_identity_nor_image_evidence():
    a = result(title="Premier titre", author="A")
    b = result(title="Autre titre", author="B")
    assert exact_search_cover_scope(a, "alice") == exact_search_cover_scope(b, "alice")
    assert project_exact_search_catalogue_cover(a, identity_id="alice").catalogue == (
        project_exact_search_catalogue_cover(b, identity_id="alice").catalogue
    )
    # The source key was minted from exact source/edition coordinates, not
    # from these coincidentally matching or different descriptive strings.


def test_no_n3_io_or_entitlement_or_url_based_document_join():
    source = (
        Path(__file__).resolve().parents[1]
        / "src/cifereca/core/search_cover_provenance.py"
    ).read_text(encoding="utf-8")
    assert "covers" in source
    assert "artifact_get(" not in source
    assert "local_book_path(" not in source
    assert "httpx" not in source
    assert "open(" not in source
    assert "thumbnail_url" in source
    assert 'tier="catalog"' in source
    assert "known_catalog_stale" in source
