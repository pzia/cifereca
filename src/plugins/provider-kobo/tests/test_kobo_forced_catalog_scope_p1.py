"""#1917: forced Kobo catalogue search cannot leak private normalized hits.

All responses are generated offline: no Kobo account, web requests, or DRM.
"""
from cifereca_kobo_catalog import KoboCatalogService
from tests.test_kobo_manifest_policy_p1 import FakeVault, session_with_vault


class PoisonedCatalogue:
    def __init__(self, session):
        self.calls = []
        self.normalizer = KoboCatalogService(
            session.client, credential_id=session.credential_id,
            provider_id=session.provider_id,
        )

    def search(self, query, *, field, limit, page, scope="all"):
        self.calls.append(scope)
        # Deliberately return personal records even for forced catalog.
        return [
            self.normalizer.normalize_item(
                {"product_id": "public", "title": "Catalogue",
                 "collection": "catalog", "owned": False},
            ),
            self.normalizer.normalize_item(
                {"product_id": "private", "title": "Owned",
                 "collection": "library", "owned": True,
                 "account_id": "alice"},
            ),
            self.normalizer.normalize_item(
                {"product_id": "archived", "title": "Archived",
                 "collection": "archived", "owned": False,
                 "account_id": "alice"},
            ),
            self.normalizer.normalize_item(
                {"product_id": "forged-public", "title": "Forged",
                 "collection": "catalog", "owned": False,
                 "account_id": "alice"},
            ),
        ]


def test_forced_catalog_drops_private_records_even_with_valid_account():
    vault = FakeVault([])
    session = session_with_vault(vault)
    session.catalog = catalog = PoisonedCatalogue(session)
    try:
        result = session.search("example", scope="catalog")
        assert catalog.calls == ["catalog"]
        assert [row.record_id for row in result] == ["public"]
        assert all(row.owned is False for row in result)
        assert vault.manifest_calls == 0 and vault.acquisition_calls == 0
    finally:
        session.close()


def test_normal_combined_search_retains_account_authority():
    vault = FakeVault([])
    session = session_with_vault(vault)
    session.catalog = catalog = PoisonedCatalogue(session)
    try:
        result = session.search("example", scope="all")
        assert catalog.calls == ["all"]
        # A verified account may still attest owned and archived records.
        assert [row.record_id for row in result] == [
            "public", "private", "archived", "forged-public",
        ]
        assert result[1].owned is True
        assert vault.manifest_calls == 0 and vault.acquisition_calls == 0
    finally:
        session.close()
