from __future__ import annotations

from cifereca.plugin_api import BookAccessFact, book_access_facts


def _kinds(row, *, identity_id=None):
    return {fact.kind for fact in book_access_facts(row, identity_id=identity_id)}


def test_local_file_is_not_inferred_to_be_owned_or_free():
    facts = book_access_facts({"state": "local", "local": True, "local_path": "/library/book.epub"})
    assert facts == (BookAccessFact("local", "library.local"),)


def test_active_entitlements_distinguish_owned_and_borrowed_rights():
    row = {
        "identity_id": "alice", "local": False,
        "possessions": [
            {"identity_id": "alice", "kind": "owned", "state": "active"},
            {"identity_id": "alice", "kind": "loan", "state": "active"},
            {"identity_id": "alice", "kind": "owned", "state": "expired"},
        ],
    }
    assert _kinds(row, identity_id="alice") == {"owned", "borrowed"}
    assert "borrowable" not in _kinds(row, identity_id="alice")


def test_identity_scope_never_inherits_another_profiles_possession():
    row = {
        "identity_ids": ["alice", "bob"],
        "possessions": [
            {"identity_id": "alice", "kind": "owned", "state": "active"},
            {"identity_id": "bob", "kind": "loan", "state": "active"},
        ],
    }
    assert _kinds(row, identity_id="alice") == {"owned"}
    assert _kinds(row, identity_id="bob") == {"borrowed"}
    assert _kinds(row, identity_id="charlie") == set()


def test_search_results_require_explicit_flags_for_borrowable_or_free():
    row = {"downloadable": True, "availability_status": "available", "source_collection": "public"}
    assert book_access_facts(row) == ()
    row["metadata"] = {"search_result": {"borrowable": True, "free": True}}
    facts = book_access_facts(row)
    assert BookAccessFact("borrowable", "search_result.borrowable") in facts
    assert BookAccessFact("free", "search_result.free") in facts


def test_unscoped_catalogue_owned_flag_does_not_claim_another_identity():
    row = {"owned": True, "metadata": {"owned": True, "free": False}}
    assert "owned" not in _kinds(row, identity_id="bob")
    assert "owned" in _kinds(row)


def test_badly_shaped_provider_metadata_does_not_create_rights():
    for row in (None, {}, {"metadata": []}, {"possessions": "owned"},
                {"possessions": [{"kind": "owned", "state": "inactive"}]},
                {"metadata": {"search_result": {"borrowable": "true", "free": 1}}}):
        assert book_access_facts(row) == ()
