"""Local description packets never cross owner or Placement boundaries."""
from cifereca.core.notice_c_purchase_library_projection import (
    PurchaseLibraryNotice, purchase_library_notice,
)
from cifereca.core.notice_c_purchase_descriptive_read import PurchaseDescriptiveNotice
from tests.test_core_p0_1072_durable_purchase_intent import prepare
from cifereca.storage import Storage


def test_local_packet_exact_placement(tmp_path):
    db = Storage(tmp_path / "local.sqlite")
    try:
        _, candidate = prepare(db)
        notice = PurchaseDescriptiveNotice(None, 1, 100, ())
        packet = PurchaseLibraryNotice("local:a", candidate.source, notice, ("a", "p"))
        row = {"key": "local:a", "identity_id": candidate.source.scope.identity_id,
               "local": True, "state": "local", "artifact_id": "a", "placement_id": "p",
               "_purchase_library_notice": packet}
        assert purchase_library_notice(row) is packet
        for field, value in (("identity_id", "B"), ("placement_id", "q"),
                             ("artifact_id", "b"), ("local", False), ("key", "other")):
            assert purchase_library_notice({**row, field: value}) is None
    finally:
        db.close()


def test_local_completed_origin_receives_product_description(tmp_path):
    from copy import deepcopy
    from cifereca.core import notice_c_job_journal as journal
    from cifereca.core.notice_c_purchase_execution import PurchaseIntentExecution
    from cifereca.core.notice_c_purchase_publication import publish_purchase_public_txn
    from cifereca.core.notice_c_purchase_association import record_purchase_association_txn
    from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
    from cifereca.core.notice_c_purchase_library_projection import project_purchase_library_notices
    from tests.test_core_p0_1072_purchase_descriptive_read import allowed
    from tests.test_core_p0_1072_durable_purchase_intent import request, set_policy
    from tests.test_core_p0_1072_nondestructive_purchase_publication import observation
    from tests.test_core_p0_3171_atomic_publication_v14 import new_source, publish, target

    db = Storage(tmp_path / "origin.sqlite")
    execution = PurchaseIntentExecution(db, clock=lambda: 100)
    try:
        snapshot, candidate = prepare(db)
        key, equivalent = new_source("A", provider="seller-a", credential="card-a", account="buyer-a")
        assert snapshot == equivalent
        path = target(tmp_path, "A")
        acquired = publish(db, tmp_path / "EBooks", path, key, snapshot)
        set_policy(db, "true")
        ref, = request(db, snapshot, candidate)
        assert execution.attest(ref, snapshot, candidate, hold_context=allowed,
                                execute=lambda *_: None, ttl_seconds=30)
        lease = execution.claim(ref, now=100, lease_seconds=30)
        assert lease is not None
        spec = journal.read_job(db, ref).spec
        with db._lock, db._connect() as sql:
            sql.execute("BEGIN IMMEDIATE")
            published = publish_purchase_public_txn(
                sql, spec, snapshot, candidate, observation(candidate, at=98), now=100,
            )
            record_purchase_association_txn(
                sql, lease, snapshot, candidate, published, observed_at=99, now=100,
            )
            journal._complete_job_txn(sql, lease, now=100)
        persist_exact_public_search_cover(db, published.source)
        row = {"key": "local:real", "identity_id": "A", "local": True,
               "state": "local", "artifact_id": acquired.artifact_id,
               "placement_id": acquired.placement_id, "title": "Titre EPUB",
               "metadata": {"description": "Résumé EPUB"}}
        original = deepcopy(row)
        project_purchase_library_notices(db, [row], identity_id="A")
        packet = purchase_library_notice(row)
        assert packet is not None and packet.source == candidate.source
        assert any(f.value == "Résumé public" for f in packet.notice.facts)
        from cifereca.core.search import filter_loaded_results, local_match_explanation
        assert filter_loaded_results([row], "Résumé public") == [row]
        assert "Résumé (catalogue public)" in local_match_explanation(row, '"Résumé public"')
        assert filter_loaded_results([row], candidate.title, field="title") == [row]
        assert filter_loaded_results([row], candidate.title, field="isbn") == []
        assert filter_loaded_results([{**row, "identity_id": "B"}],
                                     '"Résumé public"') == []

        assert {k: v for k, v in row.items() if k != "_purchase_library_notice"} == original
        row["placement_id"] = "foreign-placement"
        project_purchase_library_notices(db, [row], identity_id="A")
        assert purchase_library_notice(row) is None
        row["placement_id"] = acquired.placement_id
        with db._lock, db._connect() as sql:
            sql.execute("DELETE FROM origin_links WHERE artifact_id=?", (acquired.artifact_id,))
        project_purchase_library_notices(db, [row], identity_id="A")
        assert purchase_library_notice(row) is None and row == original
        assert path.is_file()
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        execution.close()
        db.close()
