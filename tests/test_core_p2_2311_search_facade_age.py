"""#2311: actual Core offline search API accepts optional, attested-age filters."""
from __future__ import annotations

from cifereca.core.search import KnownCatalogSearch, LocalLibrarySearch, filter_loaded_results
from tests.test_core043_progress_cache_stats import _service


def _row(item, *, age=None, kind="publisher", provider="bnf", identity="alice"):
    meta = {"languages": ["fr"], "youth_age_ranges": (
        [{"age_min": age, "age_max": age + 2, "age_kind": kind}]
        if age is not None else []
    )}
    return {
        "item_id": item, "provider_id": provider, "identity_id": identity,
        "title": "Livre commun", "author": "Auteur", "media_format": "epub",
        "metadata": meta,
    }


def test_pure_age_filter_is_opt_in_on_the_same_loaded_items():
    rows = [_row("eight", age=8), _row("ten", age=10),
            _row("unknown"), _row("inferred", age=8, kind="inferred")]
    # Filter disabled preserves all rows, including those with unknown ages.
    assert filter_loaded_results(rows, "livre") == rows
    assert [r["item_id"] for r in filter_loaded_results(rows, "livre", age=9)] == [
        "eight",
    ]
    assert [r["item_id"] for r in filter_loaded_results(
        rows, "livre", age=11, age_kind="publisher",
    )] == ["ten"]
    assert filter_loaded_results(rows, "livre", age=11, age_kind="reading") == []
    assert filter_loaded_results(rows, "livre", age=True) == []
    assert filter_loaded_results(rows, "livre", age=18) == []
    assert filter_loaded_results(rows, "auteur", field="author", age=9)[0]["item_id"] == "eight"


def test_known_and_library_search_apply_same_age_restriction_locally():
    source = [_row("eight", age=8), _row("unknown"), _row("ten", age=10)]
    calls = []
    def local_entries(*, identity_id, scan):
        calls.append(("local", identity_id, scan))
        return source if identity_id == "alice" else []
    def known_entries():
        calls.append(("catalog",))
        return [
            {**row, "catalog_entry_id": "known-" + row["item_id"],
             "local_state": "remote_catalog"}
            for row in source
        ]
    local = LocalLibrarySearch(local_entries)
    known = KnownCatalogSearch(known_entries, local_entries)
    assert [r["item_id"] for r in local.search(
        "livre", identity_id="alice", age=9,
    )] == ["eight"]
    assert [r["item_id"] for r in known.search(
        "livre", identity_id="alice", age=11,
    )] == ["ten"]
    assert [r["item_id"] for r in known.search(
        "livre", identity_id="alice", age=None,
    )] == ["eight", "unknown", "ten"]
    assert known.search("livre", identity_id="bob", age=9) == []
    assert all(entry[0] in {"local", "catalog"} for entry in calls)


def test_library_service_offline_facade_never_contacts_providers(tmp_path):
    service = _service(tmp_path)
    try:
        row = _row("edition-a", age=8)
        service.storage.catalog_entry_put({
            **row, "catalog_entry_id": "catalogue-edition-a",
            "local_state": "remote_catalog",
        })
        other = _row("edition-b", age=13)
        service.storage.catalog_entry_put({
            **other, "catalog_entry_id": "catalogue-edition-b",
            "local_state": "remote_catalog",
        })
        service.storage.catalog_entry_put({
            **_row("unproven"),
            "catalog_entry_id": "catalogue-unproven",
            "local_state": "remote_catalog",
        })
        def no_network(*args, **kwargs):
            raise AssertionError("age filtering must not access providers")
        service._sessions = {"forbidden": no_network}
        assert [r["item_id"] for r in service.search_known_catalog(
            "livre", identity_id="alice", age=9,
        )] == ["edition-a"]
        assert [r["item_id"] for r in service.search_known_catalog(
            "livre", identity_id="alice", age=14,
        )] == ["edition-b"]
        rows = service.search_known_catalog("livre", identity_id="alice")
        assert [r["item_id"] for r in service.filter_loaded_search_results(
            rows, "livre", age=9,
        )] == ["edition-a"]
        assert service.search_known_catalog("livre", identity_id="bob", age=9) == []
        assert service.search_known_catalog("livre", identity_id="alice", age=8,
                                            age_kind="reading") == []
    finally:
        service.close()
