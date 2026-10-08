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



def test_merged_local_book_never_claims_another_identity_has_the_file():
    # One canonical book row can represent a local placement for Alice
    # and an active remote entitlement for Bob.
    row = {
        "identity_id": "alice",
        "identity_ids": ["alice", "bob"],
        "local": True,
        "placements": [{"identity_id": "alice", "state": "present", "placement_id": "a-1"}],
        "possessions": [{"identity_id": "bob", "kind": "owned", "state": "active"}],
    }
    assert book_access_facts(row, identity_id="alice") == (
        BookAccessFact("local", "placement.present"),
    )
    assert book_access_facts(row, identity_id="bob") == (
        BookAccessFact("owned", "entitlement.owned"),
    )
    assert book_access_facts(row, identity_id="carol") == ()


def test_two_present_placements_attest_both_identities_independently():
    row = {
        "identity_ids": ["alice", "bob"],
        "local": True,
        "placements": [
            {"identity_id": "alice", "state": "present"},
            {"identity_id": "bob", "state": "present"},
            {"identity_id": "carol", "state": "deleted"},
        ],
    }
    for identity_id in ("alice", "bob"):
        assert _kinds(row, identity_id=identity_id) == {"local"}
    assert _kinds(row, identity_id="carol") == set()


def test_absent_placement_is_not_attested_local_access():
    for state in ("removed", "absent", "expired", "", None):
        row = {
            "identity_ids": ["alice"],
            "identity_id": "alice",
            "local": True,
            "placements": [{"identity_id": "alice", "state": state}],
        }
        assert "local" not in _kinds(row, identity_id="alice")


def test_legacy_local_rows_are_scoped_without_guessing_shared_ownership():
    assert _kinds({"local": True, "identity_id": "alice"}, identity_id="alice") == {"local"}
    assert _kinds({"local": True, "identity_id": "alice"}, identity_id="bob") == set()
    assert _kinds({"local": True, "identity_ids": ["alice"]}, identity_id="alice") == {"local"}
    assert _kinds({"local": True, "identity_ids": ["alice", "bob"]}, identity_id="bob") == set()
    assert _kinds({"local": True}, identity_id="alice") == set()
    # Unscoped listing remains compatible: it makes no claim about an identity.
    assert _kinds({"local": True}) == {"local"}



def test_merged_metadata_owned_cannot_be_attributed_to_wrong_identity():
    # The library projection merges local metadata for Alice with remote
    # entitlement metadata for Bob. Only the entitlement records its owner.
    row = {
        "identity_id": "alice",
        "identity_ids": ["alice", "bob"],
        "local": True,
        "placements": [{"identity_id": "alice", "state": "present"}],
        "metadata": {"owned": True, "search_result": {"owned": True}},
        "possessions": [{"identity_id": "bob", "kind": "owned", "state": "active"}],
    }
    assert _kinds(row, identity_id="alice") == {"local"}
    assert _kinds(row, identity_id="bob") == {"owned"}
    assert _kinds(row, identity_id="carol") == set()


def test_multi_identity_unscoped_owned_flags_do_not_invent_a_possession():
    row = {
        "identity_id": "alice",
        "identity_ids": ["alice", "bob"],
        "owned": True,
        "metadata": {"owned": True, "search_result": {"owned": True}},
    }
    assert book_access_facts(row, identity_id="alice") == ()
    assert book_access_facts(row, identity_id="bob") == ()
    assert book_access_facts(row) == ()


def test_single_identity_explicit_owned_flags_remain_supported():
    row = {
        "identity_id": "alice",
        "identity_ids": ["alice"],
        "owned": True,
        "metadata": {"owned": True},
    }
    assert BookAccessFact("owned", "provider.owned") in book_access_facts(
        row, identity_id="alice",
    )
    assert BookAccessFact("owned", "metadata.owned") in book_access_facts(
        row, identity_id="alice",
    )
    assert book_access_facts(row, identity_id="bob") == ()


def test_explicit_entitlement_ownership_remains_visible_for_both_merged_identities():
    row = {
        "identity_id": "alice",
        "identity_ids": ["alice", "bob"],
        "owned": True,
        "possessions": [
            {"identity_id": "alice", "kind": "loan", "state": "active"},
            {"identity_id": "bob", "kind": "owned", "state": "active"},
        ],
    }
    assert _kinds(row, identity_id="alice") == {"borrowed"}
    assert _kinds(row, identity_id="bob") == {"owned"}


def test_explicit_empty_placement_inventory_is_not_local_evidence():
    # A persisted catalogue entry can survive after its last local placement
    # has disappeared. The explicit inventory takes precedence over legacy
    # identity_id fallback, even for the default identity.
    for placements in ([], (), [None, {"state": "removed", "identity_id": "alice"}]):
        row = {
            "local": True, "identity_id": "alice",
            "identity_ids": ["alice"], "placements": placements,
        }
        assert book_access_facts(row, identity_id="alice") == ()
    # Legacy documents without an inventory keep backward compatibility.
    assert book_access_facts(
        {"local": True, "identity_id": "alice"}, identity_id="alice",
    ) == (BookAccessFact("local", "library.local"),)
