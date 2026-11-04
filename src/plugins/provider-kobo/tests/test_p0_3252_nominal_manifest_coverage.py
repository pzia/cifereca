"""P0 #3252/#3171: Kobo complete personal inventory is attested only by Vault.

No real network or credentials: the real KoboVaultClient paginates through
HTTPX MockTransport and the installed Kobo provider projects exact rights.
Complete coverage never authorizes negative reconciliation or a content GET.
"""
from __future__ import annotations

import httpx
import pytest

from tests.test_p0_981_kobo_source_inventory_batch import _session


def _vault(pages):
    calls = []
    def handler(request):
        assert request.url.path == "/api/v2/library/manifest"
        cursor = request.url.params.get("cursor")
        calls.append(cursor)
        index = 0 if cursor is None else int(cursor[1:])
        assert index < len(pages)
        return httpx.Response(200, json={
            "items": pages[index],
            "next_cursor": f"p{index+1}" if index+1 < len(pages) else None,
        })
    return handler, calls


@pytest.mark.parametrize("count", (0, 1, 6))
def test_real_vault_full_manifest_attests_personal_inventory(count):
    rows = [
        {"product_id": f"book-{i}", "title": f"Book {i}",
         "owned": True, "account_id": "vault-account-a"}
        for i in range(count)
    ]
    pages = [rows[:3], rows[3:]] if count > 3 else [rows]
    handler, calls = _vault(pages)
    session, account = _session(handler)
    try:
        rights = session.rights_observation_snapshot(account)
        assert len(rights.rights) == count
        assert len(calls) == len(pages)
        assert session.client.last_manifest_pagination_complete is True
        assert rights.status == "success"
        assert rights.coverage == "complete"
        assert rights.pagination_complete is True
        assert rights.claims_complete_branch is True
        assert rights.authorizes_negative_reconciliation is False
        assert rights.authorizes_mutation is False
    finally:
        session.close()


def test_paginated_vault_source_sync_cache_keeps_its_original_complete_proof():
    rows = [{"product_id": f"b-{i}", "owned": True} for i in range(6)]
    handler, calls = _vault([rows[:3], rows[3:]])
    session, account = _session(handler)
    try:
        with session.source_sync_inventory(account):
            assert len(session.library()) == 6
            assert len(calls) == 2
            first = session.rights_observation_snapshot(account)
            assert first.coverage == "complete"
            assert len(calls) == 2
            # The cached page evidence is scoped to this exact source Sync.
            session.client.last_manifest_pagination_complete = False
            second = session.rights_observation_snapshot(account)
            assert second.coverage == "complete"
            assert len(calls) == 2
        new = session.rights_observation_snapshot(account)
        assert new.coverage == "complete"
        assert len(calls) == 4
    finally:
        session.close()


@pytest.mark.parametrize("items", (
    [{"product_id": "b1", "owned": True}, {"bad": "unidentified"}],
    [{"product_id": "b1", "owned": True, "account_id": "other-borrower"}],
    [{"product_id": "b1", "owned": True}, {"product_id": "b1", "owned": True}],
    [{"product_id": "catalogue-only", "owned": False}],
))
def test_complete_vault_pagination_but_unverified_cards_stays_partial(items):
    handler, calls = _vault([items])
    session, account = _session(handler)
    try:
        rights = session.rights_observation_snapshot(account)
        assert calls == [None]
        assert rights.status == rights.coverage == "partial"
        assert rights.pagination_complete is False
        assert not rights.authorizes_negative_reconciliation
    finally:
        session.close()


def test_selected_archived_right_never_inherits_complete_manifest_coverage():
    handler, calls = _vault([[{
        "product_id": "archived-book", "owned": True,
        "archived": True, "collection": "archived",
    }]])
    session, account = _session(handler)
    try:
        full = session.rights_observation_snapshot(account)
        assert full.coverage == "complete"
        selected = session.rights_delivery_offer_snapshot(
            account, "archived-book", explicit_user_action=True,
        )
        assert selected.status == selected.coverage == "partial"
        assert selected.pagination_complete is False
        assert selected.rights[0].delivery == "unavailable"
        assert not selected.authorizes_negative_reconciliation
        assert len(calls) == 2  # No Vault descriptor POST for an archive.
    finally:
        session.close()



@pytest.mark.parametrize("count", (500, 522))
def test_large_personal_manifest_uses_all_pages_once_per_source_sync(count):
    """Synthetic large-account replay: no provider network or ACSM download."""
    rows = [
        {
            "product_id": f"book-{i:04d}", "title": f"Book {i}",
            "owned": True, "downloadable": True,
            "account_id": "vault-account-a",
        }
        for i in range(count)
    ]
    pages = [rows[i:i + 200] for i in range(0, count, 200)]
    handler, calls = _vault(pages)
    session, account = _session(handler)
    try:
        with session.source_sync_inventory(account):
            library = session.library()
            rights = session.rights_observation_snapshot(account)
            downloadable = session.downloadable_resource_snapshot(account)
            assert len(library) == count
            assert len(rights.rights) == count
            assert len(downloadable.resources) == count
            assert rights.status == "success"
            assert rights.coverage == "complete"
            assert rights.pagination_complete is True
            assert downloadable.status == "success"
            assert downloadable.coverage == "complete"
            # Rights and materializable file references are two projections
            # of the one already-fetched manifest, never two extra GETs.
            assert calls == [None, "p1", "p2"]
            assert session.client.last_manifest_pagination_complete is True
            assert session.client.last_manifest_page_attempts == len(pages)
        # Leaving the scope removes the cache: a later explicit read is
        # fresh, and is not permitted to reuse stale page or borrower proof.
        rights_again = session.rights_observation_snapshot(account)
        assert len(rights_again.rights) == count
        assert calls == [None, "p1", "p2", None, "p1", "p2"]
    finally:
        session.close()


def test_large_manifest_cross_page_duplicate_is_quarantined_not_first_wins():
    """Distinct page/revision rows cannot mint one arbitrarily owned book."""
    rows = [
        {
            "product_id": f"book-{i:04d}", "title": f"Book {i}",
            "owned": True, "downloadable": True,
            "account_id": "vault-account-a",
        }
        for i in range(522)
    ]
    rows[-1] = {**rows[-1], "product_id": rows[0]["product_id"]}
    pages = [rows[i:i + 200] for i in range(0, len(rows), 200)]
    handler, calls = _vault(pages)
    session, account = _session(handler)
    try:
        with session.source_sync_inventory(account):
            observed = session.rights_observation_snapshot(account)
            files = session.downloadable_resource_snapshot(account)
            assert len(observed.rights) == 520
            assert len(files.resources) == 520
            assert all(x.edition_id != "book-0000" for x in observed.rights)
            assert observed.status == observed.coverage == "partial"
            assert observed.pagination_complete is False
            assert not observed.authorizes_negative_reconciliation
            assert calls == [None, "p1", "p2"]
        # Physical file authority remains in Core; these are just positive
        # inventory assertions, never proof of a completed local delivery.
    finally:
        session.close()
