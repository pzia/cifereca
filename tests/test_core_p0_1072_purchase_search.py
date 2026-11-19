"""Purchase public facts search retains native data and field provenance."""
from cifereca.core.search import filter_loaded_results, local_match_explanation
from cifereca.core.notice_c_purchase_library_projection import PurchaseLibraryNotice
from cifereca.core.notice_c_purchase_descriptive_read import PurchaseDescriptiveNotice, PurchaseDescriptiveFact
from tests.test_core_p0_1072_durable_purchase_intent import prepare
from cifereca.storage import Storage


def test_purchase_search_exact_scope(tmp_path):
    db = Storage(tmp_path / "search.sqlite")
    try:
        _, candidate = prepare(db)
        source = candidate.source
        scope = source.scope
        fact = PurchaseDescriptiveFact("description", "Archipel violet", "seller-a", "source", "revision", 100)
        notice = PurchaseDescriptiveNotice(None, 1, 100, (fact,))
        row = {"key": "remote", "identity_id": scope.identity_id,
               "provider_id": scope.provider_id, "item_id": source.edition_id,
               "source": "private_catalog_observation", "state": "remote_catalog",
               "local": False, "title": "Titre natif",
               "source_refs": [{"identity_id": scope.identity_id,
                                "provider_id": scope.provider_id,
                                "credential_id": scope.credential_id,
                                "account_id": scope.account_id,
                                "item_id": source.edition_id, "kind": "purchase"}],
               "_purchase_library_notice": PurchaseLibraryNotice("remote", source, notice)}
        assert filter_loaded_results([row], "archipel violet") == [row]
        assert "Résumé (catalogue public)" in local_match_explanation(row, "archipel")
        assert not filter_loaded_results([row], "archipel", field="title")
        assert not filter_loaded_results([{**row, "identity_id": "B"}], "archipel")
        assert filter_loaded_results([row], "titre natif") == [row]
    finally:
        db.close()
