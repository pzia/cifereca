"""#4662: normal lender borrow, late synthetic delivery, independent native send.

Real claim/publication/transfer writers and isolated FS/SQLite, no provider
network, ADEPT, protected payload or implied recipient supplier authority.
"""
from hashlib import sha256
from pathlib import Path

import pytest

from cifereca.core.borrow_action_scope import borrow_claim_key
from cifereca.core.canonical_publication import publish_completed_acquisition
from cifereca.storage import Storage
from tests.test_core_p0_3171_atomic_publication_v14 import new_source
from tests.test_core_p1_3275_canonical_transfer_v14 import rows
from tests.test_core_p1_4662_live_lender_preview import harness
from tests.test_core_p1_4988_normal_lender_borrow import setup, facade_execute


@pytest.mark.parametrize("scenario", ["collision", "sqlite_crash", "revoked", "changed_bytes",
                                      "wrong_right", "missing_origin", "wrong_kind"])
def test_confirmed_lender_claim_and_separate_canonical_copy(harness, tmp_path, monkeypatch, scenario):
    service, lender, session, notice, candidate, _scope = setup(harness)
    reader = service.current_identity
    db = service.storage
    monkeypatch.setattr("cifereca.paths.acquisition_lock_file",
                        lambda: tmp_path / "shared-acquisition.lock")
    monkeypatch.setattr("cifereca.core.canonical_transfer.acquisition_lock_file",
                        lambda: tmp_path / "shared-acquisition.lock")
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "simulation"))
    from plugin_scenarios import create

    dataset = tmp_path / "dataset"
    manifest = create(dataset)
    fixture = dataset / "optimizer-valid.epub"
    payload = fixture.read_bytes()
    digest = sha256(payload).hexdigest()
    assert digest == manifest["files"][fixture.name]
    scope = dict(requesting_identity_id=reader.identity_id, borrowing_account=lender,
                 exact_notice=notice)
    # No supplier mutation without normal borrow consent, and this independent
    # consent alone never creates a recipient copy or delivers bytes.
    assert facade_execute(service, candidate, notice, confirmed=False)["status"] == "blocked"
    assert session.actions == 0
    assert facade_execute(service, candidate, notice)["status"] == "confirmed"
    assert session.actions == 1 and service.current_identity is reader
    key = borrow_claim_key(lender, notice)
    receipt = db.loan_borrow_state(key)
    assert receipt["status"] == "confirmed" and receipt["verified_loan_id"] == "loan-synthetic"
    assert service.resolve_cross_profile_lender_placement(**scope).status == "unavailable"
    assert service.send_confirmed_lender_local_copy(confirmed=True, **scope)["status"] == "blocked"
    assert db.placements() == []

    # Synthetic late delivery: bind the generated unprotected fixture to the
    # exact lender and provider-observed loan. Not a real HTTP/ADEPT receipt.
    root = tmp_path / "EBooks"
    src = root / "alex" / "Acquisitions" / "Loan.epub"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(payload)
    connection_id = db.provider_connection_put({
        "identity_id": lender.identity_id, "provider_id": lender.provider_id,
        "credential_id": lender.credential_id, "label": "Synthetic", "enabled": True,
    })
    db.provider_account_put({
        "identity_id": lender.identity_id, "provider_id": lender.provider_id,
        "credential_id": lender.credential_id, "connection_id": connection_id,
        "account_id": lender.unique_id, "label": "Synthetic lender",
    })
    source, snapshot = new_source(
        lender.identity_id, provider=lender.provider_id, credential=lender.credential_id,
        account=lender.unique_id, branch=notice.docbase, edition=notice.record_id,
        right="other-loan" if scenario == "wrong_right" else receipt["verified_loan_id"],
        kind="purchase" if scenario == "wrong_kind" else "loan",
    )
    from cifereca.locking import InterProcessLock
    with InterProcessLock(tmp_path / "shared-acquisition.lock"):
        published = publish_completed_acquisition(
            db, library_root=root, path=src, source=source, snapshot=snapshot,
            final_sha256=digest, delivery_receipt_ref="synthetic-late-delivery",
        )
    assert rows(db) == {
        "artifacts": 1, "placements": 1, "source_references": 1,
        "origin_links": 1, "entitlements": len(db.entitlements()),
    }
    before = rows(db)
    rights_before = db.entitlements()
    ledger_before = db.acquisitions()
    exact = dict(lender_placement_id=published.placement_id, **scope)
    if scenario in {"wrong_right", "missing_origin", "wrong_kind"}:
        # Poison the legacy hints to appear exact: a typed reference still
        # takes precedence. Missing proof is not a reason to use these hints.
        with db._lock, db._connect() as sql:
            sql.execute("UPDATE placements SET provider_id=?,source_item_id=? WHERE placement_id=?",
                        (lender.provider_id, notice.record_id, published.placement_id))
            if scenario == "missing_origin":
                sql.execute("DELETE FROM origin_links WHERE source_reference_id=?",
                            (published.source_reference_id,))
        assert service.resolve_cross_profile_lender_placement(**scope).status == "unavailable"
        assert service.preflight_cross_profile_loan_transfer(**exact).transfer == "blocked"
        denied = service.send_cross_profile_borrowed_placement(confirmed=True, **exact)
        assert denied["status"] == "blocked" and denied["retry_borrow"] is False
        assert len(db.placements()) == 1 and db.loan_borrow_state(key) == receipt
        assert session.actions == 1 and src.read_bytes() == fixture.read_bytes() == payload
        return

    again = service.resolve_cross_profile_lender_placement(**scope)
    assert again.status == "present" and again._placement_id == published.placement_id

    def capture(confirmed=True):
        return service.capture_admitted_lender_local_copy(confirmed=confirmed, **exact)

    assert capture(False) is None
    lease = capture()
    assert lease is not None
    destination = root / "hugues" / "Acquisitions"
    destination.mkdir(parents=True, exist_ok=True)
    occupied = destination / "Loan.epub"
    if scenario == "collision":
        # Same bytes, unrelated existing destination: don't adopt by name/SHA.
        occupied.write_bytes(payload)
    elif scenario == "sqlite_crash":
        with db._lock, db._connect() as sql:
            sql.execute(
                "CREATE TRIGGER fixture_copy_failure BEFORE INSERT ON origin_links "
                "WHEN NEW.origin_kind='cifereca' "
                "BEGIN SELECT RAISE(ABORT,'synthetic_transfer_failure'); END"
            )
        blocked = service.execute_admitted_lender_local_copy(lease)
        assert blocked["status"] == "blocked" and blocked["retry_borrow"] is False
        assert rows(db) == before
        assert occupied.read_bytes() == payload
        with db._lock, db._connect() as sql:
            sql.execute("DROP TRIGGER fixture_copy_failure")
    elif scenario == "revoked":
        service._source_context_epoch += 1
        blocked = service.execute_admitted_lender_local_copy(lease)
        assert blocked["status"] == "blocked" and blocked["retry_borrow"] is False
        assert rows(db) == before and not list(destination.iterdir())
    else:
        src.write_bytes(b"unrelated bytes")
        blocked = service.execute_admitted_lender_local_copy(lease)
        assert blocked["status"] == "blocked" and blocked["retry_borrow"] is False
        assert rows(db) == before and not list(destination.iterdir())
        assert src.read_bytes() == b"unrelated bytes"
        assert db.loan_borrow_state(key) == receipt
        assert session.actions == 1 and fixture.read_bytes() == payload
        return

    # New SQLite connection and fresh *copy* consent: the loan claim remains
    # spent. A revoked or interrupted copy cannot repeat the supplier POST.
    database = db.path
    db.close()
    service.storage = db = Storage(database)
    assert db.loan_borrow_state(key) == receipt
    assert db.loan_borrow_claim_once(key, "different-process-claim")[0] is False
    lease = capture()
    assert lease is not None
    done = service.execute_admitted_lender_local_copy(lease)
    assert done["status"] == "complete" and done["transfer"] == "completed"
    assert done["retry_borrow"] is False
    recipient, = [p for p in db.placements() if p["identity_id"] == reader.identity_id]
    target = Path(recipient["path"])
    assert target.parent == destination
    assert target.read_bytes() == src.read_bytes() == fixture.read_bytes() == payload
    assert not target.samefile(src)
    if scenario == "collision":
        assert target != occupied and occupied.read_bytes() == payload
        assert not target.samefile(occupied)
    else:
        assert target == occupied
    assert recipient["artifact_id"] == published.artifact_id
    assert recipient["metadata"]["materialized_from"] == published.placement_id
    with db._lock, db._connect() as sql:
        assert sql.execute(
            "SELECT identity_id,provider_id,reference_kind,synchronizable "
            "FROM source_references ORDER BY identity_id"
        ).fetchall() == [
            (lender.identity_id, lender.provider_id, "loan", 1),
            (reader.identity_id, "cifereca", "internal", 0),
        ]
        assert sql.execute(
            "SELECT origin_kind,from_identity_id FROM origin_links WHERE identity_id=? "
            "ORDER BY origin_kind", (reader.identity_id,),
        ).fetchall() == [("cifereca", None), ("transfer", lender.identity_id)]
    after = rows(db)
    assert after["artifacts"] == 1 and after["placements"] == 2
    assert after["source_references"] == 2 and after["origin_links"] == 3
    assert service.execute_admitted_lender_local_copy(capture())["status"] == "complete"
    assert rows(db) == after and db.loan_borrow_state(key) == receipt
    assert db.entitlements() == rights_before
    # The native copy deliberately retains a local-transfer checkpoint in
    # acquisition_ledger. It must never turn into a supplier acquisition.
    final_ledger = db.acquisitions()
    old_ids = {row["acquisition_id"] for row in ledger_before}
    assert [row for row in final_ledger if row["acquisition_id"] in old_ids] == ledger_before
    checkpoint, = [row for row in final_ledger if row["acquisition_id"] not in old_ids]
    assert (checkpoint["identity_id"], checkpoint["provider_id"], checkpoint["credential_id"],
            checkpoint["account_id"], checkpoint["strategy"], checkpoint["status"],
            checkpoint["entitlement_id"], checkpoint["artifact_id"], checkpoint["attempt_count"]) == (
        reader.identity_id, "cifereca", "local", reader.identity_id, "local_transfer",
        "local_transfer_published", None, None, 0,
    )
    assert checkpoint["metadata"]["destination"] == str(target)
    assert checkpoint["metadata"]["scope"]["artifact_id"] == published.artifact_id
    assert checkpoint["metadata"]["scope"]["from_placement_id"] == published.placement_id
    assert facade_execute(service, candidate, notice)["status"] == "blocked"
    assert session.actions == 1 and service.current_identity is reader
