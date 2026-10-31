"""#3185: interrupted mixed-reference relink retains eight independent origins."""
import sqlite3

from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, new_source, publish, register, store,
)
from tests.test_core_p0_3185_canonical_backup_relink import rig, backup
from tests.test_core_p0_3185_dedup_supervisor import inspect
from tests.test_core_p0_3719_prepare_duplicate import _catalogue, _journal


def test_eight_origins_survive_partial_relink_and_retry(rig):
    storage, root, paths, pubs, _lock = rig
    for n in range(5):
        provider, credential, account = (
            f"historic-seller-{n}", f"historic-card-{n}", f"historic-buyer-{n}"
        )
        key, snapshot = new_source(
            "A", provider=provider, credential=credential,
            account=account, edition=f"historical-{n}",
        )
        register(storage, "A", provider, credential, account=account)
        publish(storage, root, paths[0], key, snapshot)
    _catalogue(rig)
    backup(rig)
    receipt = _journal(rig)
    with storage._lock, storage._connect() as db:
        origins = db.execute("SELECT * FROM origin_links ORDER BY origin_link_id").fetchall()
        sources = db.execute("SELECT * FROM source_references ORDER BY source_reference_id").fetchall()
        assert len(origins) == len(sources) == 8
        db.execute(
            "CREATE TRIGGER block_relink BEFORE UPDATE OF local_path "
            "ON backup_items BEGIN SELECT RAISE(ABORT,'interrupted_relink'); END"
        )
    blocked = inspect(rig, action="commit", confirmed=True)
    assert (blocked.status, blocked.reason) == ("blocked", "relink_refused")
    assert storage.catalog_entry_get("catalogue:seller-b")["placement_id"] == pubs[0].placement_id
    assert storage.placement_get(pubs[1].placement_id)["state"] == "present"
    assert all(path.read_bytes() == BYTES for path in paths)
    with storage._lock, storage._connect() as db:
        assert db.execute("SELECT * FROM origin_links ORDER BY origin_link_id").fetchall() == origins
        assert db.execute("SELECT * FROM source_references ORDER BY source_reference_id").fetchall() == sources
        db.execute("DROP TRIGGER block_relink")
    recovered = inspect(rig, action="commit", confirmed=True)
    assert recovered.status == "retired"
    with storage._lock, storage._connect() as db:
        assert db.execute("SELECT * FROM origin_links ORDER BY origin_link_id").fetchall() == origins
        assert db.execute("SELECT * FROM source_references ORDER BY source_reference_id").fetchall() == sources
        assert db.execute(
            "SELECT metadata_json FROM acquisition_ledger WHERE acquisition_id=?",
            (receipt,),
        ).fetchone() is not None
    assert storage.placement_get(pubs[1].placement_id) is None
    assert storage.placement_get(pubs[2].placement_id)["state"] == "present"
    assert not paths[1].exists()
    assert paths[0].read_bytes() == paths[2].read_bytes() == BYTES
