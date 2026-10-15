"""#1867 — persisted purchases belong to a store, not the loans page.

Only an authenticated account-bound catalog/entitlement can evidence an owned
remote book. The provider's search claims or existence of an EPUB alone cannot.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.core.store_ownership_projection import owned_store_inventory_counts
from cifereca.models import Account
from tests.test_sync_state_contract import _service
from tests.test_core_p0_1862_numilog_store_mirror import _connect


@pytest.mark.parametrize("books", [0, 1, 5])
def test_numilog_personal_purchases_count_as_store_books_not_loans(
    tmp_path, books,
):
    service = _service(tmp_path)
    try:
        _account, _session, calls = _connect(service, number=books)
        service.mirror_personal_libraries()
        first = service.identity_statistics("default")
        assert first["owned_store_books"] == books
        assert first["owned_store_sources"] == 1
        assert first["known_loans"] is None
        assert first["remote_owned_rights"] == 0  # old Kobo entitlement-only metric
        assert first["local_books"] == 0
        assert service.identity_statistics("alex")["owned_store_books"] == 0
        assert calls == ["library"]
        # A second snapshot cannot duplicate owned rights or fetch again.
        second = service.identity_statistics("default")
        assert second["owned_store_books"] == first["owned_store_books"]
        assert calls == ["library"]
    finally:
        service.close()


def test_multi_store_rights_do_not_alias_loans_or_local_artifacts(tmp_path):
    service = _service(tmp_path)
    try:
        numilog, _session, _calls = _connect(service, number=2)
        service.mirror_personal_libraries()
        kobo = Account(
            display_name="Kobo", unique_id="vault-account",
            provider_id="kobo", credential_id="vault", identity_id="default",
        )
        service.accounts.append(kobo)
        service.storage.provider_connection_put({
            "identity_id": "default", "provider_id": "kobo",
            "credential_id": "vault", "enabled": True,
        })
        service.storage.entitlement_put({
            "identity_id": "default", "provider_id": "kobo",
            "credential_id": "vault", "account_id": "vault-account",
            "item_id": "product-1", "entitlement_type": "owned",
            "state": "active",
        })
        # Same product/revision in another row must not count twice.
        service.storage.entitlement_put({
            "identity_id": "default", "provider_id": "kobo",
            "credential_id": "vault", "account_id": "vault-account",
            "item_id": "product-1", "entitlement_type": "owned",
            "state": "active", "revision_id": "rev-2",
        })
        # A loan from an unrelated provider is never a purchased store right.
        service.storage.entitlement_put({
            "identity_id": "default", "provider_id": "paris",
            "credential_id": "reader", "account_id": "card",
            "item_id": "borrowed", "entitlement_type": "loan",
            "state": "active",
        })
        stats = service.identity_statistics("default")
        assert stats["owned_store_books"] == 3
        assert stats["owned_store_sources"] == 2
        assert stats["local_books"] == 0
        assert stats["remote_owned_rights"] == 1
        assert stats["known_loans"] is None
    finally:
        service.close()


def test_numilog_generic_owned_search_claim_does_not_certify_purchase(tmp_path):
    service = _service(tmp_path)
    try:
        _account, _session, calls = _connect(service, number=0)
        service.storage.catalog_entries_upsert_many([
            {
                "catalog_entry_id": "public-claimed-owned",
                "provider_id": "numilog", "identity_id": "default",
                "item_id": "fake-purchased",
                "local_state": "remote_owned",
                "metadata": {
                    "source_collection": "catalog",
                    "source_credential": "numilog-private",
                    "product_url": "https://example.invalid/?token=SECRET",
                },
            },
            {
                "catalog_entry_id": "wrong-credential",
                "provider_id": "numilog", "identity_id": "default",
                "item_id": "fake-different-account",
                "local_state": "remote_owned",
                "metadata": {
                    "source_collection": "library",
                    "source_credential": "other-secret-credential",
                },
            },
            {
                "catalog_entry_id": "local-only",
                "provider_id": "numilog", "identity_id": "default",
                "item_id": "only-file",
                "local_state": "local",
                "metadata": {
                    "source_collection": "library",
                    "source_credential": "numilog-private",
                },
            },
            {
                "catalog_entry_id": "other-identity",
                "provider_id": "numilog", "identity_id": "alex",
                "item_id": "foreign-owned",
                "local_state": "remote_owned",
                "metadata": {
                    "source_collection": "library",
                    "source_credential": "numilog-private",
                },
            },
        ])
        facts = service.identity_statistics("default")
        assert facts["owned_store_books"] == 0
        assert facts["owned_store_sources"] == 1
        assert calls == []
        for secret in ("SECRET", "other-secret-credential", "fake-purchased"):
            assert secret not in repr(facts)
    finally:
        service.close()


def test_rights_are_identity_and_credential_bound_even_after_rebinding(tmp_path):
    service = _service(tmp_path)
    try:
        account, _session, _calls = _connect(service, number=2)
        service.mirror_personal_libraries()
        assert service.identity_statistics("default")["owned_store_books"] == 2
        service.storage.provider_connection_put({
            "identity_id": "alex", "provider_id": "numilog",
            "credential_id": account.credential_id, "enabled": True,
        })
        assert service.identity_statistics("default")["owned_store_books"] == 0
        assert service.identity_statistics("alex")["owned_store_books"] == 0
        # Another ambiguous account must not inherit the previous rights.
        service.accounts.append(Account(
            provider_id="numilog", credential_id=account.credential_id,
            identity_id="default", unique_id="second",
        ))
        assert service.identity_statistics("default")["owned_store_books"] == 0
    finally:
        service.close()


def test_inactive_entitlements_and_unbound_purchases_are_not_current_rights(
    tmp_path,
):
    service = _service(tmp_path)
    try:
        service.storage.entitlement_put({
            "identity_id": "default", "provider_id": "kobo",
            "credential_id": "no-active-binding",
            "item_id": "owned-unbound", "entitlement_type": "owned",
            "state": "active",
        })
        service.storage.entitlement_put({
            "identity_id": "default", "provider_id": "numilog",
            "credential_id": "no-active-binding",
            "item_id": "owned-archived", "entitlement_type": "owned",
            "state": "archived",
        })
        assert service.identity_statistics("default")["owned_store_books"] == 0
        assert service.identity_statistics("default")["owned_store_sources"] == 0
    finally:
        service.close()


def test_public_projection_tolerates_missing_bindings_without_network():
    rows = [
        NS(identity_id="one", provider_id="numilog",
           credential_id="private-account", unique_id="one"),
        NS(identity_id="one", provider_id="kobo",
           credential_id="second-private", unique_id="two"),
    ]
    storage = NS(
        identity_for_connection=lambda provider, credential:
            (_ for _ in ()).throw(RuntimeError("database-unavailable")),
        entitlements=lambda **_kwargs: [],
        catalog_entries=lambda **_kwargs: [],
    )
    result = owned_store_inventory_counts(storage, "one", rows)
    assert result == {"owned_store_books": 0, "owned_store_sources": 0}
    assert "private" not in repr(result)
