"""#3680 / P1 #4979: exact-book catalogue tie breaks must be stable and read-only.

Only a canonical same-book identity supplied by Core can enable deterministic
selection. Unscoped bibliographic candidates remain ambiguous.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from cifereca.core.covers import CoverCandidate, resolve_cover
from tests.test_core_library_placement_identity_scope import _service


A = "https://public.example.org/catalog-a.png"
B = "https://public.example.org/catalog-b.png"


def cover(url, source, strength=80):
    return CoverCandidate(url, "catalog", source, "catalogued:edition-exact", strength)


@pytest.mark.parametrize("reverse", (False, True))
def test_configured_priority_wins_only_for_attested_exact_book(reverse):
    items = [cover(A, "cat.a"), cover(B, "cat.b")]
    if reverse:
        items.reverse()
    result = resolve_cover(
        items, attested_book_identity="artifact:sha256:verified",
        catalog_priorities={"cat.a": 1, "cat.b": 9},
    )
    assert result.status == "selected"
    assert result.url == B and result.source == "cat.b"
    assert result.evidence_id == "catalogued:edition-exact"
    assert result.metadata()["candidate_count"] == 2
    assert {row.evidence_strength for row in result.candidates} == {80}
    # A supplied preference is NOT a proof that unrelated editions match.
    unknown = resolve_cover(items, catalog_priorities={"cat.a": 1, "cat.b": 9})
    assert unknown.status == "ambiguous" and unknown.url == ""


@pytest.mark.parametrize("ranks", (
    None, {}, {"cat.a": 0, "cat.b": 0},
    {"cat.a": 7, "cat.b": 7}, {"cat.a": True, "cat.b": 5},
    {"cat.a": 10001, "cat.b": 5},
))
def test_equal_or_unavailable_priority_uses_stable_catalogue_id(ranks):
    first = [cover(A, "cat.a"), cover(B, "cat.b")]
    forward = resolve_cover(first, attested_book_identity="scope:exact", catalog_priorities=ranks)
    reverse = resolve_cover(list(reversed(first)), attested_book_identity="scope:exact",
                            catalog_priorities=ranks)
    assert forward.url == reverse.url == A
    assert forward.status == reverse.status == "selected"


def test_strong_evidence_and_tier_always_override_catalogue_preference():
    items = [cover(A, "cat.a", 81), cover(B, "cat.b", 80)]
    assert resolve_cover(items, attested_book_identity="book:verified",
                         catalog_priorities={"cat.b": 1000}).url == A
    items.append(CoverCandidate("https://origin.example/cover.png",
                                "origin", "source", evidence_strength=0))
    assert resolve_cover(items, attested_book_identity="book:verified",
                         catalog_priorities={"cat.b": 1000}).tier == "origin"
    items.append(CoverCandidate("file:///cache/embedded.png",
                                "embedded", "epub", evidence_strength=0))
    assert resolve_cover(items, attested_book_identity="book:verified").tier == "embedded"


def test_same_catalogue_conflicting_urls_remain_ambiguous_even_with_book_scope():
    items = [cover(A, "cat.a"), cover(B, "cat.a")]
    result = resolve_cover(items, attested_book_identity="scope:book",
                           catalog_priorities={"cat.a": 100})
    assert result.status == "ambiguous" and not result.url
    assert len(result.candidates) == 2


def test_same_url_is_unique_and_provenance_is_order_independent():
    items = [cover(A, "cat.b"), cover(A, "cat.a")]
    for order in (items, list(reversed(items))):
        result = resolve_cover(order, attested_book_identity="scope:book")
        assert result.url == A and result.source == "cat.a"
        assert result.metadata()["candidate_count"] == 1


@pytest.mark.parametrize("bad_context", ("", "x" * 161, "wrong\nscope", None, 9))
def test_invalid_or_absent_identity_never_silently_resolves_conflicting_books(bad_context):
    result = resolve_cover([cover(A, "cat.a"), cover(B, "cat.b")],
                           attested_book_identity=bad_context,
                           catalog_priorities={"cat.a": 1, "cat.b": 999})
    assert result.status == "ambiguous"
    assert result.url == ""


def _entitlement(catalog):
    return {
        "provider_id": "numilog", "identity_id": "alice",
        "credential_id": "card-a", "account_id": "borrower-a",
        "item_id": "edition-a", "entitlement_id": "receipt-1",
        "entitlement_type": "owned", "state": "active",
        "metadata": {"title": "Same verified edition",
                     "catalog_cover": {"url": A if catalog == "cat.a" else B,
                                       "source": catalog, "evidence_id": "exact:edition-a",
                                       "evidence_strength": 80}},
    }


@pytest.mark.parametrize("reverse", (False, True))
def test_real_library_projection_preserves_winner_on_refresh_and_exact_scope(
    tmp_path, monkeypatch, reverse,
):
    service = _service(tmp_path)
    try:
        alice, bob = _entitlement("cat.a"), _entitlement("cat.b")
        # These two independent observations have exactly the same scoped
        # entitlement receipt, not just a shared title or ISBN.
        rows = [alice, bob] if not reverse else [bob, alice]
        monkeypatch.setattr(service.storage, "entitlements", lambda: deepcopy(rows))
        first, = service.library_entries(identity_id="alice", scan=False)
        assert first["thumbnail_url"] == A
        assert first["metadata"]["cover_resolution"]["source"] == "cat.a"
        assert first["metadata"]["catalog_cover"]["url"] == A
        assert first["metadata"]["cover_resolution"]["candidate_count"] == 2
        second, = service.library_entries(identity_id="alice", scan=False)
        assert second["thumbnail_url"] == first["thumbnail_url"]
        assert second["metadata"]["catalog_cover"] == first["metadata"]["catalog_cover"]
        assert service.library_entries(identity_id="bob", scan=False) == []
    finally:
        service.close()


def test_no_cross_account_merge_or_title_based_tie(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        alice = _entitlement("cat.a")
        other = _entitlement("cat.b")
        other["credential_id"] = "card-other"
        other["account_id"] = "borrower-other"
        other["entitlement_id"] = "receipt-other"
        monkeypatch.setattr(service.storage, "entitlements",
                            lambda: [deepcopy(alice), deepcopy(other)])
        cards = service.library_entries(identity_id="alice", scan=False)
        assert len(cards) == 2
        assert {card["thumbnail_url"] for card in cards} == {A, B}
        assert {card["possessions"][0]["account_id"] for card in cards} == {
            "borrower-a", "borrower-other"
        }
    finally:
        service.close()
