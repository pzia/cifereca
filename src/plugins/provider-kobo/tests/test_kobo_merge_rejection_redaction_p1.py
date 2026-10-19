"""#1917: public Kobo search rejections carry only fixed, bounded diagnostic facts.

No HTTP, credentials, purchased items or access to any Kobo service.
"""
from cifereca_kobo_catalog import KoboCatalogService

SECRET = "PRIVATE_TOKEN=https://vault.invalid/acquire?session=PRIVATE"


class FakeVault:
    base_url = "https://vault.example"

    def __init__(self, errors):
        self.last_search_merge_rejections = errors
        self.calls = []

    def search(self, query, **kwargs):
        self.calls.append((query, kwargs))
        return {"items": []}


def test_merge_rejections_do_not_relay_private_vendor_diagnostic_keys():
    client = FakeVault([
        {
            "source": "catalog", "type": "invalid_shape",
            "reason": "missing_product_id",
            "private_url": SECRET, "record_id": "PRIVATE_BOOK",
            "extra": {"account": "PRIVATE_ACCOUNT"},
        },
        {
            "source": "library", "type": "invalid_scope",
            "reason": SECRET, "message": SECRET,
        },
        {"source": "PRIVATE_ACCOUNT", "type": "invalid_scope", "reason": SECRET},
        {"source": "library", "type": SECRET, "reason": "item_not_object"},
        {"source": "catalog", "type": "invalid_shape", "reason": {"token": SECRET}},
        {"PRIVATE": SECRET},
    ])
    service = KoboCatalogService(client, credential_id="test")
    assert service.search("Doyle", scope="all") == []
    assert service.last_search_rejections == [
        {"source": "catalog", "type": "invalid_shape", "reason": "missing_product_id"},
        {"source": "library", "type": "invalid_scope", "reason": "unverified_rejection"},
        {"source": "catalog", "type": "invalid_shape", "reason": "unverified_rejection"},
    ]
    assert SECRET not in repr(service.last_search_rejections)
    assert "PRIVATE" not in repr(service.last_search_rejections)
    assert client.calls == [("Doyle", {"field": "all", "scope": "all", "limit": 50, "page": 0})]


def test_large_or_non_list_rejections_are_bounded_and_ignored():
    many = [
        {"source": "catalog", "type": "invalid_shape", "reason": "item_not_object"}
        for _ in range(10000)
    ]
    client = FakeVault(many)
    service = KoboCatalogService(client, credential_id="test")
    assert service.search("Doyle") == []
    assert len(service.last_search_rejections) == 128

    class Poison:
        def __iter__(self):
            raise AssertionError(SECRET)
    client.last_search_merge_rejections = Poison()
    assert service.search("Doyle") == []
    assert service.last_search_rejections == []


def test_bad_rejection_never_changes_public_item_rights():
    client = FakeVault([
        {"source": "library", "type": "invalid_scope", "reason": SECRET},
    ])
    service = KoboCatalogService(client, credential_id="test")
    assert service.search("Doyle", scope="catalog") == []
    assert service.last_search_rejections == [
        {"source": "library", "type": "invalid_scope", "reason": "unverified_rejection"},
    ]
