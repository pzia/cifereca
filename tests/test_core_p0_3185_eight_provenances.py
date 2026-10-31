"""#3185: eight independent origin proofs survive explicit mixed-reference dedup."""
import json

from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, new_source, publish, register, rows, store, target,
)
from tests.test_core_p0_3185_canonical_backup_relink import rig, backup
from tests.test_core_p0_3185_dedup_supervisor import inspect
from tests.test_core_p0_3719_prepare_duplicate import _catalogue, _journal


def test_eight_origin_links_survive_mixed_reference_relink_and_retirement(rig, tmp_path):
    storage, root, paths, pubs, _ = rig
    # Base fixture: three provenance links. Each new independently attested
    # supplier account publishes the SAME SHA to an existing profile path.
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
    with storage._lock, storage._connect() as db:
        before_origins = db.execute(
            "SELECT * FROM origin_links ORDER BY origin_link_id"
        ).fetchall()
        before_sources = db.execute(
            "SELECT * FROM source_references ORDER BY source_reference_id"
        ).fetchall()
    assert len(before_origins) == len(before_sources) == 8
    _catalogue(rig)
    backup(rig)
    receipt = _journal(rig)
    result = inspect(rig, action="commit", confirmed=True)
    assert (result.status, result.catalogue_relinked,
            result.backup_relinked, result.receipt_relinked) == (
                "retired", 1, 1, 1,
            )
    with storage._lock, storage._connect() as db:
        assert db.execute(
            "SELECT * FROM origin_links ORDER BY origin_link_id"
        ).fetchall() == before_origins
        assert db.execute(
            "SELECT * FROM source_references ORDER BY source_reference_id"
        ).fetchall() == before_sources
        checkpoint = json.loads(db.execute(
            "SELECT metadata_json FROM acquisition_ledger WHERE acquisition_id=?",
            (receipt,),
        ).fetchone()[0])
    assert checkpoint["path"] == str(paths[0])
    assert storage.catalog_entry_get("catalogue:seller-b")["placement_id"] == pubs[0].placement_id
    assert next(r for r in storage.backup_list() if r["credential_id"] == "card-b")["local_path"] == str(paths[0])
    assert storage.placement_get(pubs[1].placement_id) is None
    assert storage.placement_get(pubs[2].placement_id)["state"] == "present"
    assert not paths[1].exists()
    assert paths[0].read_bytes() == paths[2].read_bytes() == BYTES
