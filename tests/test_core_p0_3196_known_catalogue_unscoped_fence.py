"""P0 #3196: identity-less Known Catalog search never merges private accounts.

All catalogue rows synthetic; no SQLite mutation, provider call or network.
"""
from cifereca.core.search import KnownCatalogSearch


def test_missing_identity_fails_closed_before_reading_any_private_catalogue():
    calls = []

    def catalogue():
        calls.append("catalogue")
        return [
            {"catalog_entry_id": "a", "identity_id": "alice",
             "provider_id": "seller", "item_id": "edition",
             "title": "ALICE_PRIVATE_TITLE"},
            {"catalog_entry_id": "b", "identity_id": "bob",
             "provider_id": "seller", "item_id": "edition",
             "title": "BOB_PRIVATE_TITLE"},
        ]

    def local(*, identity_id, scan):
        calls.append(("local", identity_id, scan))
        return []

    search = KnownCatalogSearch(catalogue, local)
    assert search.search("private") == []
    assert search.search("private", identity_id=None) == []
    assert search.search("private", identity_id="") == []
    assert search.search("private", identity_id=True) == []
    assert calls == []  # The cross-profile SQL reader is never called.

    alice = search.search("private", identity_id="alice")
    bob = search.search("private", identity_id="bob")
    assert [row["title"] for row in alice] == ["ALICE_PRIVATE_TITLE"]
    assert [row["title"] for row in bob] == ["BOB_PRIVATE_TITLE"]
    assert calls == [
        "catalogue", ("local", "alice", False),
        "catalogue", ("local", "bob", False),
    ]



def test_no_profile_local_search_never_calls_unscoped_legacy_reader():
    from cifereca.core.search import LocalLibrarySearch

    calls = []
    def private_library(*, identity_id, scan):
        calls.append((identity_id, scan))
        return [
            {"identity_id": "alice", "title": "ALICE_PRIVATE"},
            {"identity_id": "bob", "title": "BOB_PRIVATE"},
        ]

    reader = LocalLibrarySearch(private_library)
    assert reader.search("private", identity_id=None) == []
    assert reader.search("private", identity_id="") == []
    assert reader.search("private", identity_id=0) == []
    assert calls == []
