"""#1346: personal Kobo search results belong to the verified account only.

Offline projection test: no Vault HTTP, acquisition, file access or credentials in
diagnostics. The public catalogue remains visible independently of credentials.
"""
from __future__ import annotations

from cifereca.plugin_api import Account
from cifereca_kobo_catalog import KoboCatalogService
from tests.test_kobo_manifest_policy_p1 import FakeVault, session_with_vault


class Catalog:
    def __init__(self, session, hits):
        self.normalizer = KoboCatalogService(
            session.client, credential_id=session.credential_id,
            provider_id=session.provider_id,
        )
        self.hits = hits
        self.calls = []

    def search(self, query, *, field, limit, page):
        self.calls.append((query, field, limit, page))
        return [
            self.normalizer.normalize_item(row, default_collection=source)
            for source, row in self.hits
        ]


def _hits():
    return [
        ("catalog", {"product_id": "public", "title": "Public",
                     "collection": "catalog", "owned": False}),
        ("library", {"product_id": "own", "title": "Own",
                     "account_id": "alice", "owned": True}),
        ("library", {"product_id": "legacy-own", "title": "Legacy",
                     "owned": True}),
        ("library", {"product_id": "PRIVATE_FOREIGN", "title": "Private",
                     "account_id": "bob", "owned": True, "revision_id": "PRIVATE_REV"}),
        ("library", {"product_id": "PRIVATE_INVALID", "account_id": None,
                     "owned": True}),
        ("library", {"product_id": "PRIVATE_DENIAL", "account_id": "bob",
                     "owned": False, "collection": "library"}),
    ]


def test_search_filters_foreign_and_invalid_account_but_keeps_public_and_legacy():
    vault = FakeVault([])
    session = session_with_vault(vault)
    session.catalog = catalog = Catalog(session, _hits())
    try:
        result = session.search("book")
        assert [item.record_id for item in result] == [
            "public", "own", "legacy-own",
        ]
        assert result[0].owned is False
        assert result[1].owned is True and result[2].owned is True
        assert catalog.calls == [("book", "all", 50, 0)]
        assert vault.manifest_calls == 0 and vault.acquisition_calls == 0
    finally:
        session.close()


def test_search_does_not_resurrect_personal_results_after_disconnect_or_rebind():
    vault = FakeVault([])
    session = session_with_vault(vault)
    session.catalog = Catalog(session, _hits())
    try:
        session.connected = False
        assert [item.record_id for item in session.search("x")] == ["public"]

        session.connected = True
        session._accounts = [Account(
            unique_id="alice", display_name="Private",
            provider_id="kobo", credential_id="different-credential",
        )]
        assert [item.record_id for item in session.search("x")] == ["public"]

        session._accounts = [Account(
            unique_id="alice", display_name="Private",
            provider_id="kobo", credential_id=session.credential_id,
        )]
        assert [item.record_id for item in session.search("x")] == [
            "public", "own", "legacy-own",
        ]
        assert vault.manifest_calls == 0 and vault.acquisition_calls == 0
    finally:
        session.close()
