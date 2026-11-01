"""#2797: Library source facets follow scoped Core provenance, without network.

Both the real SQLite Library projection and adversarial loaded rows are used.
The provider filter must not infer provenance from title, filename or SHA.
"""
from cifereca.core.search import filter_loaded_results
from cifereca.gui.query_model import QueryState
from tests.test_core_p0_2797_originlink_library_projection import (
    _entitlement, _local_artifact, _source_origin,
)
from tests.test_core_library_placement_identity_scope import _service


def test_numilog_originlink_is_selectable_even_when_local_is_primary(tmp_path):
    service = _service(tmp_path)
    try:
        artifact, _ = _local_artifact(service, tmp_path, owner="alice")
        _source_origin(
            service, identity="alice", provider="numilog",
            credential="card-a", account="buyer-a",
            edition="edition-a", artifact_id=artifact, token="numilog-a",
        )
        _entitlement(
            service, identity="alice", credential="card-a",
            account="buyer-a", edition="edition-a",
        )
        rows = service.library_entries(identity_id="alice", scan=False)
        assert len(rows) == 1
        assert rows[0]["provider_id"] == "local"
        assert rows[0]["providers"] == ["local", "numilog"]
        assert QueryState(providers=("numilog",)).filter(rows) == rows
        assert QueryState(providers=("LOCAL",)).filter(rows) == rows
        assert QueryState(providers=("bnf-pret",)).filter(rows) == []
        assert QueryState("Source-owned", field="title",
                          providers=("numilog",), types=("epub",)).filter(rows) == rows
    finally:
        service.close()


def test_same_sha_across_identites_never_inherits_other_owner_source(tmp_path):
    service = _service(tmp_path)
    try:
        artifact, _ = _local_artifact(service, tmp_path, owner="alice")
        _local_artifact(service, tmp_path, owner="bob")
        _source_origin(
            service, identity="alice", provider="bnf-pret",
            credential="card-a", account="borrower-a",
            edition="edition-a", artifact_id=artifact, token="loan-a",
        )
        alice = service.library_entries(identity_id="alice", scan=False)
        bob = service.library_entries(identity_id="bob", scan=False)
        assert len(alice) == len(bob) == 1
        assert QueryState(providers=("bnf-pret",)).filter(alice) == alice
        assert QueryState(providers=("bnf-pret",)).filter(bob) == []
        assert QueryState(providers=("local",)).filter(bob) == bob
    finally:
        service.close()


def test_secondary_source_requires_both_scoped_ref_and_projection():
    baseline = {
        "key": "artifact:sha256:fixture", "identity_id": "alice",
        "source": "catalog", "local": True,
        "provider_id": "local", "title": "Synthetic",
        "providers": ["local", "numilog"],
    }
    valid_ref = {"provider_id": "numilog", "identity_id": "alice",
                 "item_id": "exact-edition"}
    checks = [
        {},
        {"source_refs": []},
        {"source_refs": [dict(valid_ref, identity_id="bob")]},
        {"source_refs": [dict(valid_ref, item_id="")]},
        {"source_refs": ["numilog"]},
        {"source_refs": [valid_ref], "providers": "numilog"},
        {"source_refs": [valid_ref], "providers": {"numilog": True}},
        {"source_refs": [valid_ref], "source": "public_search"},
        {"source_refs": [valid_ref], "source": ["catalog"]},
        {"source_refs": [valid_ref], "source": {"kind": "catalog"}},
        {"source_refs": [valid_ref], "providers": ["local", 8]},
        {"source_refs": [dict(valid_ref, provider_id="kobo")]},
    ]
    for changes in checks:
        row = {**baseline, **changes}
        assert filter_loaded_results([row], providers=("numilog",)) == []
        assert filter_loaded_results([row], providers=("local",)) == [row]
    admitted = {**baseline, "source_refs": [valid_ref]}
    assert filter_loaded_results([admitted], providers=("numilog",)) == [admitted]
    assert filter_loaded_results([admitted], providers=("foreign",)) == []


def test_primary_provider_and_online_search_filtering_unchanged():
    rows = [
        {"key": "remote", "provider_id": "numilog", "title": "Remote"},
        {"key": "plain", "provider_id": "local", "title": "Local"},
        {"key": "online", "provider_id": "kobo", "title": "Online",
         "source": "public_search", "identity_id": "alice",
         "providers": ["kobo", "numilog"],
         "source_refs": [{"provider_id": "numilog", "identity_id": "alice",
                          "item_id": "forged"}]},
    ]
    assert [r["key"] for r in filter_loaded_results(rows, providers=("numilog",))] == ["remote"]
    assert [r["key"] for r in filter_loaded_results(rows, providers=("local",))] == ["plain"]
    assert filter_loaded_results(rows, providers=("missing",)) == []
    assert filter_loaded_results(rows) == rows


def test_local_placement_keeps_local_filter_when_primary_is_provider():
    row = {
        "key": "artifact:sha256:real", "source": "catalog",
        "identity_id": "alice", "provider_id": "bnf-pret",
        "providers": ["bnf-pret"], "local": True,
        "source_refs": [{"identity_id": "alice", "provider_id": "bnf-pret",
                         "item_id": "edition"}],
    }
    assert QueryState(providers=("local",)).filter([row]) == [row]
    assert QueryState(providers=("bnf-pret",)).filter([row]) == [row]
