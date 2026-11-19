"""Independent historical documentary counters in the real SQLite Sync broker."""
from dataclasses import replace

from cifereca.gui.sync_view import sync_source_last_cycle_summary
from cifereca.storage import Storage
from tests.test_core_0978_source_state import fake_broker


def test_documentary_handoff_is_not_sync_or_acquisition_failure(tmp_path):
    path = tmp_path / "cycle.sqlite"
    counts = dict(requested=5, admitted=2, deferred=2, unrequested=3)
    with_storage = Storage(path)
    try:
        broker = fake_broker(with_storage)
        sid = broker.sources()[0].source_id
        broker.service.synchronize_account_source = lambda *a, **kw: {
            "status": "success",
            "canonical_rights": {"status": "success", "purchase_enrichment": counts},
        }
        result = broker.synchronize_source(sid)
        assert result.ok and result.changed == 0
        assert result.facts["purchase_enrichment"] == counts
        source = broker.sources()[0]
        assert source.last_cycle.status == "success"
        assert dict(source.last_cycle.purchase_enrichment) == counts
        label = sync_source_last_cycle_summary(source)
        assert "2 différées" in label and "3 non inscrites" in label
        assert "pas un décompte de fiches terminées" in label
        assert "Résultats de réception locale : non attestés" in label
        malformed = replace(source.last_cycle, purchase_enrichment=tuple(
            {**counts, "admitted": True}.items()))
        assert "Enrichissement documentaire" not in sync_source_last_cycle_summary(
            replace(source, last_cycle=malformed))
    finally:
        with_storage.close()
    db = Storage(path)
    try:
        broker = fake_broker(db)
        assert dict(broker.sources()[0].last_cycle.purchase_enrichment) == counts
        broker.service.synchronize_account_source = lambda *a, **kw: {
            "status": "success", "canonical_rights": {
                "status": "success", "purchase_enrichment": {
                    **counts, "unrequested": "PRIVATE_TOKEN",
                },
            },
        }
        result = broker.synchronize_source(sid)
        assert result.ok and "purchase_enrichment" not in result.facts
        assert broker.sources()[0].last_cycle.purchase_enrichment is None
        assert "PRIVATE_TOKEN" not in db.metadata_get("sync.source.state." + sid)
    finally:
        db.close()
