"""P0 #3196: without a selected identity Library never unions private profiles.

Uses real SQLite and two synthetic profile entitlements, no provider traffic.
"""
from __future__ import annotations

import json

from tests.test_core_library_placement_identity_scope import _service


def test_no_selected_identity_exposes_neither_account_and_does_not_scan(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        for owner in ("alice", "bob"):
            service.storage.entitlement_put({
                "provider_id": "kobo", "credential_id": owner + "-card",
                "account_id": owner + "-buyer", "identity_id": owner,
                "item_id": "shared-edition", "entitlement_type": "owned",
                "state": "active",
                "metadata": {
                    "title": owner.upper() + "_PRIVATE_TITLE",
                    "author": owner.upper() + "_PRIVATE_AUTHOR",
                },
            })
        service.current_identity = None
        monkeypatch.setattr(
            service, "catalog_scan",
            lambda: (_ for _ in ()).throw(
                AssertionError("unscoped Library must not scan storage")
            ),
        )
        assert service.library_entries(scan=False) == []
        assert service.library_entries(scan=True) == []
        # Empty explicit selection must not turn into a cross-profile view,
        # whether or not a caller requests a background filesystem scan.
        assert service.library_entries(identity_id="", scan=False) == []
        assert service.library_entries(identity_id="", scan=True) == []

        # This fence does not prohibit intentionally selected identities.
        alice = service.library_entries(identity_id="alice", scan=False)
        bob = service.library_entries(identity_id="bob", scan=False)
        assert len(alice) == len(bob) == 1
        assert alice[0]["title"] == "ALICE_PRIVATE_TITLE"
        assert bob[0]["title"] == "BOB_PRIVATE_TITLE"
        assert "BOB_PRIVATE" not in json.dumps(alice)
        assert "ALICE_PRIVATE" not in json.dumps(bob)
    finally:
        service.close()


def test_selected_identity_does_not_authorize_implicit_core_library_read(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        # A GTK-selected profile is not a valid admission token for a
        # headless/queued Core read, even if it is currently selected.
        service.current_identity = next(
            identity for identity in service.identities
            if identity.identity_id == "alice"
        )
        def forbidden(*_args, **_kwargs):
            raise AssertionError("implicit library read must not scan or query storage")
        monkeypatch.setattr(service, "catalog_scan", forbidden)
        for method in ("artifacts", "placements", "catalog_entries", "entitlements"):
            monkeypatch.setattr(service.storage, method, forbidden)
        for scope in (None, "", " ", False, 123, [], {}):
            assert service.library_entries(identity_id=scope, scan=True) == []
        assert service.library_entries(scan=True) == []
    finally:
        service.close()
