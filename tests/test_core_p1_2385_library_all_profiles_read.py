"""P1 #2385: global Library read does not make identity a read ACL.

The composition must use each canonical per-profile projection without
merging same-SHA placements or scanning/mutating the underlying library.
"""
from types import SimpleNamespace

from cifereca.services import LibraryService


def _service(identities, books):
    service = object.__new__(LibraryService)
    service.identities = [SimpleNamespace(identity_id=i) for i in identities]
    calls = []

    def library_entries(*, identity_id=None, scan=False):
        calls.append((identity_id, scan))
        return list(books.get(identity_id, []))

    service.library_entries = library_entries
    return service, calls


def test_all_profiles_keeps_same_artifact_in_distinct_owner_rows():
    shared = "sha256:abcdef"
    alice = {"key": shared, "title": "One edition", "placements": [{"placement_id": "a"}]}
    bob = {"key": shared, "title": "One edition", "placements": [{"placement_id": "b"}]}
    service, calls = _service(["alice", "bob"], {"alice": [alice], "bob": [bob]})

    rows = service.library_entries_all_profiles()
    assert rows == [
        {"identity_id": "alice", "entry": alice},
        {"identity_id": "bob", "entry": bob},
    ]
    assert rows[0]["entry"] is alice and rows[1]["entry"] is bob
    assert calls == [("alice", False), ("bob", False)]


def test_all_profiles_keeps_unknown_empty_profiles_and_never_falls_back():
    service, calls = _service(
        ["alice", "bob", "alice", "", None],
        {"alice": [], "bob": [{"key": "remote:b", "source": "bob"}]},
    )
    assert service.library_entries_all_profiles() == [
        {"identity_id": "bob", "entry": {"key": "remote:b", "source": "bob"}},
    ]
    assert calls == [("alice", False), ("bob", False)]


def test_global_library_read_uses_neither_selected_identity_nor_side_effects():
    service, calls = _service(
        ["bob", "alice"],
        {"bob": [{"key": "b"}], "alice": [{"key": "a"}]},
    )
    service.current_identity = SimpleNamespace(identity_id="alice")
    service.catalog_scan = lambda: (_ for _ in ()).throw(
        AssertionError("No scan or implicit acquisition")
    )
    assert [row["identity_id"] for row in service.library_entries_all_profiles()] == [
        "bob", "alice"
    ]
    assert calls == [("bob", False), ("alice", False)]
