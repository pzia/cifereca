"""Historical enrichment state is not a right, live admission or provider error."""
from copy import deepcopy
from types import SimpleNamespace

from cifereca.core import notice_c_job_journal as journal
from cifereca.core.canonical_sync_stage import persist_attested_source_references
from cifereca.core.notice_c_purchase_library_projection import (
    project_purchase_library_notices, purchase_library_status, purchase_status_label,
)
from cifereca.gui.library_rows import project_library_rows
from cifereca.storage import Storage
from tests.test_core_p0_1072_durable_purchase_intent import prepare, request, set_policy


def test_pending_status_is_scoped_and_separate_from_inventory(tmp_path):
    db = Storage(tmp_path / "status.sqlite")
    try:
        snapshot, candidate = prepare(db)
        persist_attested_source_references(db, snapshot)
        source = candidate.source
        scope = source.scope
        row = {
            "key": "observation:fixture", "identity_id": scope.identity_id,
            "provider_id": scope.provider_id, "item_id": source.edition_id,
            "source": "private_catalog_observation", "state": "remote_catalog",
            "local": False, "title": "Titre natif", "author": "",
            "source_refs": [{
                "identity_id": scope.identity_id, "provider_id": scope.provider_id,
                "credential_id": scope.credential_id, "account_id": scope.account_id,
                "item_id": source.edition_id, "kind": "observation",
            }], "metadata": {}, "possessions": [],
        }
        original = deepcopy(row)
        project_purchase_library_notices(db, [row], identity_id="A")
        assert row == original
        set_policy(db, "true")
        ref, = request(db, snapshot, candidate)
        project_purchase_library_notices(db, [row], identity_id="A")
        packet = purchase_library_status(row)
        assert packet.state == "pending" and packet.attempts == 0
        assert purchase_library_status({**row, "identity_id": "B"}) is None
        rows, _ = project_library_rows(
            [row], row_factory=lambda *args: SimpleNamespace(values=args),
            provider_display_name=str, availability_label=lambda _: "",
            backup_status_label=str, format_date=str,
        )
        assert purchase_status_label(packet) in rows[0].values[3]
        assert rows[0]._purchase_library_status == packet
        assert {k: v for k, v in row.items() if k != "_purchase_library_status"} == original
        assert journal.read_job(db, ref).state == "pending"
        set_policy(db, "false")
        project_purchase_library_notices(db, [row], identity_id="A")
        assert purchase_library_status(row).state == "paused"
        with db._lock, db._connect() as sql:
            sql.execute("UPDATE provider_connections SET enabled=0 WHERE credential_id='card-a'")
        project_purchase_library_notices(db, [row], identity_id="A")
        assert row == original
        assert db.entitlements() == [] and db.acquisitions() == [] and db.placements() == []
    finally:
        db.close()
