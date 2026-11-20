"""#773: consume generated publication JDD with the existing Core writers."""
from hashlib import sha256
import json
from pathlib import Path

from cifereca.core.canonical_publication import publish_completed_acquisition
from cifereca.core.convergence_verified_presence import plan_existing_verified_presence
from cifereca.storage import Storage
from tests.test_core_p0_3171_atomic_publication_v14 import new_source, register
from tests.test_core_p1_3275_canonical_transfer_v14 import rows, transfer


def test_generated_publication_chain_is_real_core_sqlite_witness(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "simulation"))
    from plugin_scenarios import create

    dataset = tmp_path / "dataset"
    manifest = create(dataset)
    cases = json.loads((dataset / "publication-cases.json").read_text())
    chain = cases["chains"]["purchase_transfer_purchase_transfer"]
    expected = chain["expected"]
    fixture = chain["payload"]["fixture"]
    payload = (dataset / fixture).read_bytes()
    digest = sha256(payload).hexdigest()
    assert digest == chain["payload"]["sha256"] == manifest["files"][fixture]

    root = tmp_path / "EBooks"
    paths = {}
    for placement in chain["placements"]:
        path = root / placement["relative_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        paths[placement["identity"]] = path
    db = Storage(tmp_path / "state.sqlite")
    monkeypatch.setattr(
        "cifereca.core.canonical_transfer.acquisition_lock_file",
        lambda: tmp_path / "shared-acquisition.lock",
    )
    try:
        sources = {}
        for identity in ("A", "B"):
            lower = identity.lower()
            scope = dict(provider="seller-" + lower, credential="card-" + lower,
                         account="buyer-" + lower)
            register(db, identity, scope["provider"], scope["credential"], account=scope["account"])
            sources[identity] = new_source(identity, **scope)
        db.identity_put({"identity_id": "C", "directory_name": "C", "label": "C"})

        def purchase(identity):
            source, snapshot = sources[identity]
            return publish_completed_acquisition(
                db, library_root=root, path=paths[identity],
                source=source, snapshot=snapshot, final_sha256=digest,
                delivery_receipt_ref="synthetic-delivery-" + identity,
                adept_receipt_ref="synthetic-adept-" + identity,
            )

        a = purchase("A")
        ab = transfer(db, root, paths["A"], paths["B"], "A", "B", transfer_id="A-to-B")
        b = purchase("B")
        bc = transfer(db, root, paths["B"], paths["C"], "B", "C", transfer_id="B-to-C")
        assert a.artifact_id == ab.artifact_id == b.artifact_id == bc.artifact_id
        before = rows(db)
        for field in ("artifacts", "placements", "origin_links"):
            assert before[field] == expected[field]
        assert before["entitlements"] == 0
        with db._lock, db._connect() as sql:
            rights = sql.execute(
                "SELECT identity_id FROM source_references "
                "WHERE reference_kind='purchase' ORDER BY identity_id"
            ).fetchall()
        assert [row[0] for row in rights] == expected["independent_rights"]
        assert not transfer(db, root, paths["A"], paths["B"], "A", "B",
                            transfer_id="A-to-B").changed
        assert not transfer(db, root, paths["B"], paths["C"], "B", "C",
                            transfer_id="B-to-C").changed
        assert rows(db)["origin_links"] - before["origin_links"] == expected["replay_additional_links"]
        assert rows(db) == before
        assert all(path.read_bytes() == payload for path in paths.values())

        def verified(identity):
            _, snapshot = sources[identity]
            decision, = plan_existing_verified_presence(
                db, snapshot, library_root=root,
            ).decisions
            return decision.provider_satisfied

        assert verified("A") and verified("B")
        # Only remove the isolated fixture copies: a missing B cannot borrow
        # A's presence; C never had a provider purchase to restore.
        paths["B"].unlink()
        paths["C"].unlink()
        assert verified("A") and not verified("B")
        assert paths["A"].read_bytes() == payload
        assert rows(db) == before
        assert (dataset / fixture).read_bytes() == payload
    finally:
        db.close()
