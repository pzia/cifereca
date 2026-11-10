"""#3196: read-only UI rights must not import the Kobo manifest writer."""
from __future__ import annotations

import ast
from types import SimpleNamespace

from cifereca.core.search_owned_evidence import scoped_purchase_states


def test_generic_purchase_states_is_read_only_and_identity_scoped():
    rows = [
        {"identity_id": "alice", "provider_id": "kobo", "credential_id": "a",
         "item_id": "edition", "entitlement_type": "owned", "state": "active"},
        {"identity_id": "bob", "provider_id": "kobo", "credential_id": "b",
         "item_id": "foreign", "entitlement_type": "owned", "state": "active"},
        {"identity_id": "alice", "provider_id": "numilog", "credential_id": "n",
         "item_id": "other", "entitlement_type": "owned", "state": "archived"},
        {"identity_id": "alice", "provider_id": "kobo", "credential_id": "a",
         "item_id": "archive", "entitlement_type": "owned", "state": "archived"},
    ]
    calls = []
    def read(*, provider_id):
        calls.append(provider_id)
        return rows
    service = SimpleNamespace(storage=SimpleNamespace(entitlements=read))
    assert scoped_purchase_states(service, identity_id="alice", provider_id="kobo") == {
        ("a", "edition"): "active", ("a", "archive"): "archived"
    }
    assert calls == ["kobo"]
    assert scoped_purchase_states(service, identity_id="", provider_id="kobo") == {}
    assert calls == ["kobo"]


def test_kobo_legacy_rights_reader_is_absent_from_service():
    from cifereca.services import LibraryService
    assert not hasattr(LibraryService, "kobo_personal_right_states")
