"""P0 #3909: historically committed staging must become final without new GET.

Fixtures use a real v14 SQLite database, a synthetic provider and the same
source synchronization/receipt machinery as live Core, without network.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cifereca.core import convergence_execution
from cifereca.core.sync_coordination import synchronize_account_source
from tests.test_core_p0_3171_selected_candidate_finalization import (
    rig, _isolate_delivery_lock, _prepare, _native,
)
from tests.test_core_p0_3171_atomic_publication_v14 import SHA, BYTES


@pytest.fixture(autouse=True)
def _stock_shared_acquisition_lock(monkeypatch, tmp_path):
    # The executor's lock has already been redirected by the canonical
    # candidate fixture; the independent stock guard must refer to the same
    # synthetic lock, not the host machine's real acquisition lock.
    monkeypatch.setattr(
        "cifereca.core.canonical_staging_stock.acquisition_lock_file",
        lambda: tmp_path / "state" / "acquisition.lock",
    )


def _legacy_placement(rig, monkeypatch):
    service, db, root = rig
    _prepare(service)
    gets = []

    def candidate(*args, **kwargs):
        gets.append(kwargs["delivery_offer_id"])
        return _native(*args, **kwargs)

    service.session.rights_selected_delivery_candidate = candidate
    genuine = convergence_execution.promote_completed_delivery
    genuine_cleanup = convergence_execution.cleanup_published_staging
    # Reproduce the PRE-#3912 Core behavior: publish the temporary path,
    # and retain its bytes even after the B/receipt commit.
    monkeypatch.setattr(
        convergence_execution, "promote_completed_delivery",
        lambda _storage, *, source, completed, library_root: completed.path,
    )
    monkeypatch.setattr(
        convergence_execution, "cleanup_published_staging",
        lambda *_args, **_kwargs: None,
    )
    first = synchronize_account_source(service, service.account)
    monkeypatch.setattr(
        convergence_execution, "promote_completed_delivery", genuine,
    )
    monkeypatch.setattr(
        convergence_execution, "cleanup_published_staging", genuine_cleanup,
    )
    assert first["canonical_execution"]["downloaded"] == 1
    assert gets == ["signed-right-offer"]
    assert len(db.placements()) == 1
    old = Path(db.placements()[0]["path"])
    assert old.read_bytes() == BYTES
    assert old.is_relative_to(root / "A" / ".canonical-delivery")
    return service, db, root, old, gets


def test_stock_repair_requires_positive_persisted_source_observation(
    rig, monkeypatch,
):
    """A durable receipt and SHA alone cannot repair a revoked source proof."""
    from cifereca.core import canonical_staging_stock as stock

    service, db, root, staged, gets = _legacy_placement(rig, monkeypatch)
    placement = db.placements()[0]
    with db._lock, db._connect() as sql:
        source_ref, original = sql.execute(
            "SELECT source_reference_id,evidence_ref FROM source_references "
            "WHERE provider_id=? AND identity_id=?",
            (service.source.scope.provider_id, service.source.scope.identity_id),
        ).fetchone()
        assert original
        # Validate the same legitimate historical receipt before corrupting A.
        row, proof = stock._record(sql, service.source, placement["placement_id"])
        assert row[0] == str(staged)
        assert proof["schema"] == "canonical.completed-delivery.v1"
        sql.execute(
            "UPDATE source_references SET evidence_ref='' "
            "WHERE source_reference_id=?", (source_ref,),
        )

    with db._lock, db._connect() as sql:
        with pytest.raises(ValueError, match="canonical_stock_exact_origin_missing"):
            stock._record(sql, service.source, placement["placement_id"])
    assert staged.read_bytes() == BYTES
    assert db.placements()[0] == placement
    assert gets == ["signed-right-offer"]

    # Restoring the very same provider proof permits normal non-destructive
    # recovery: the earlier refusal did not damage SQLite or staged bytes.
    with db._lock, db._connect() as sql:
        sql.execute(
            "UPDATE source_references SET evidence_ref=? "
            "WHERE source_reference_id=?", (original, source_ref),
        )
        restored, _proof = stock._record(
            sql, service.source, placement["placement_id"],
        )
    assert restored[0] == str(staged)
    assert staged.read_bytes() == BYTES
    assert gets == ["signed-right-offer"]


def test_stock_relocated_under_same_placement_and_origin_without_second_get(
    rig, monkeypatch,
):
    service, db, root, old, gets = _legacy_placement(rig, monkeypatch)
    original_placement = db.placements()[0]["placement_id"]
    entries_before = db.catalog_entries()
    assert len(entries_before) == 1

    second = synchronize_account_source(service, service.account)
    assert second["canonical_execution"]["recovered"] == 1
    assert gets == ["signed-right-offer"]
    current = db.placements()[0]
    final = Path(current["path"])
    assert current["placement_id"] == original_placement
    assert final.parent == root / "A" / "Acquisitions"
    assert final.read_bytes() == BYTES
    assert not old.exists()
    assert db.catalog_entries()[0]["catalog_entry_id"] == entries_before[0]["catalog_entry_id"]
    assert db.catalog_entries()[0]["placement_id"] == original_placement
    assert db.catalog_entries()[0]["metadata"]["path"] == str(final)
    with db._lock, db._connect() as sql:
        refs = sql.execute(
            "SELECT o.origin_kind,o.source_reference_id "
            "FROM origin_links AS o",
        ).fetchall()
        assert len(refs) == 1 and refs[0][0] == "provider"
        journal = sql.execute(
            "SELECT status,artifact_id FROM acquisition_ledger "
            "WHERE acquisition_id LIKE 'canonical-intake:%'",
        ).fetchone()
        assert journal == ("complete", "sha256:" + SHA)

    third = synchronize_account_source(service, service.account)
    assert third["canonical_execution"]["present_attested"] == 1
    assert gets == ["signed-right-offer"]
    assert db.placements()[0]["path"] == str(final)


def test_stock_refuses_unbounded_exact_provider_origin_claims(
    rig, monkeypatch,
):
    """More than 512 same-source OriginLinks cannot select a receipt by order.

    A damaged/imported journal may carry one genuine Core receipt and
    arbitrarily many nonmatching provider claims to the same SHA. Do not
    promote or discard historical staging bytes until provenance is bounded.
    """
    service, db, root, old, gets = _legacy_placement(rig, monkeypatch)
    before = db.placements()[0]
    with db._lock, db._connect() as sql:
        origin = sql.execute(
            "SELECT identity_id,artifact_id,source_reference_id,"
            "created_at,updated_at FROM origin_links "
            "WHERE origin_kind='provider' LIMIT 1",
        ).fetchone()
        assert origin is not None
        sql.executemany(
            "INSERT INTO origin_links("
            "origin_link_id,origin_kind,identity_id,artifact_id,"
            "source_reference_id,evidence_ref,created_at,updated_at"
            ") VALUES(?,?,?,?,?,?,?,?)",
            [
                (f"untrusted-provider-claim-{i}", "provider",
                 origin[0], origin[1], origin[2], f"not-core-receipt-{i}",
                 origin[3], origin[4])
                for i in range(512)
            ],
        )
    # 512 unrelated links plus the original legitimate one exceed the
    # bounded receipt window. No arbitrary SELECT ordering may decide B.
    refused = synchronize_account_source(service, service.account)
    assert refused["canonical_execution"]["failed"] == 1
    assert refused["canonical_execution"]["recovered"] == 0
    assert refused["canonical_execution"]["downloaded"] == 0
    assert gets == ["signed-right-offer"]
    assert db.placements()[0] == before
    assert old.read_bytes() == BYTES
    assert not list((root / "A" / "Acquisitions").glob("*"))

    with db._lock, db._connect() as sql:
        sql.execute(
            "DELETE FROM origin_links "
            "WHERE origin_link_id LIKE 'untrusted-provider-claim-%'",
        )
    resumed = synchronize_account_source(service, service.account)
    assert resumed["canonical_execution"]["recovered"] == 1
    assert resumed["canonical_execution"]["downloaded"] == 0
    assert gets == ["signed-right-offer"]
    assert Path(db.placements()[0]["path"]).parent == root / "A" / "Acquisitions"
    assert not old.exists()


def test_stock_missing_exact_checkpoint_preserves_bytes_and_requires_review(
    rig, monkeypatch,
):
    service, db, root, old, gets = _legacy_placement(rig, monkeypatch)
    with db._lock, db._connect() as sql:
        key, serialized = sql.execute(
            "SELECT acquisition_id,metadata_json FROM acquisition_ledger "
            "WHERE acquisition_id LIKE 'canonical-intake:%'",
        ).fetchone()
        bad = json.loads(serialized)
        bad["source"][-1] = "loan"
        sql.execute(
            "UPDATE acquisition_ledger SET metadata_json=? WHERE acquisition_id=?",
            (json.dumps(bad), key),
        )
    denied = synchronize_account_source(service, service.account)
    assert denied["canonical_execution"]["failed"] == 1
    assert gets == ["signed-right-offer"]
    assert db.placements()[0]["path"] == str(old)
    assert old.read_bytes() == BYTES
    assert list((root / "A" / "Acquisitions").glob("*")) == []
    with db._lock, db._connect() as sql:
        sql.execute(
            "UPDATE acquisition_ledger SET metadata_json=? WHERE acquisition_id=?",
            (serialized, key),
        )
    restored = synchronize_account_source(service, service.account)
    assert restored["canonical_execution"]["recovered"] == 1
    assert gets == ["signed-right-offer"]


def test_stock_crash_after_copy_keeps_old_row_then_reuses_exact_final(
    rig, monkeypatch,
):
    service, db, root, old, gets = _legacy_placement(rig, monkeypatch)
    from cifereca.core import canonical_staging_stock as stock

    original = stock._record
    counter = []

    def fail_commit(*args, **kwargs):
        counter.append(1)
        if len(counter) == 2:
            raise ValueError("synthetic_sql_failure_with_private_coordinates")
        return original(*args, **kwargs)

    monkeypatch.setattr(stock, "_record", fail_commit)
    refused = synchronize_account_source(service, service.account)
    assert refused["canonical_execution"]["failed"] == 1
    assert db.placements()[0]["path"] == str(old)
    assert old.read_bytes() == BYTES
    finals = list((root / "A" / "Acquisitions").glob("*.pdf"))
    assert len(finals) == 1 and finals[0].read_bytes() == BYTES

    monkeypatch.setattr(stock, "_record", original)
    resumed = synchronize_account_source(service, service.account)
    assert resumed["canonical_execution"]["recovered"] == 1
    assert gets == ["signed-right-offer"]
    assert db.placements()[0]["path"] == str(finals[0])
    assert not old.exists()


def test_stock_destination_symlink_cannot_redirect_files_to_other_directory(
    rig, monkeypatch,
):
    service, db, root, old, gets = _legacy_placement(rig, monkeypatch)
    outside = root.parent / "untrusted"
    outside.mkdir()
    dest = root / "A" / "Acquisitions"
    dest.symlink_to(outside, target_is_directory=True)
    refused = synchronize_account_source(service, service.account)
    assert refused["canonical_execution"]["failed"] == 1
    assert gets == ["signed-right-offer"]
    assert list(outside.iterdir()) == []
    assert db.placements()[0]["path"] == str(old)
    dest.unlink()
    fixed = synchronize_account_source(service, service.account)
    assert fixed["canonical_execution"]["recovered"] == 1
    assert gets == ["signed-right-offer"]


def test_stock_repairs_valid_B_even_if_postcommit_intake_flag_was_lost(
    rig, monkeypatch,
):
    """OriginLink/Placement are authority, not an optional journal status."""
    service, db, root, old, gets = _legacy_placement(rig, monkeypatch)
    with db._lock, db._connect() as sql:
        sql.execute(
            "UPDATE acquisition_ledger SET status='canonical_receipt_staged',"
            "artifact_id=NULL WHERE acquisition_id LIKE 'canonical-intake:%'",
        )
    repaired = synchronize_account_source(service, service.account)
    assert repaired["canonical_execution"]["recovered"] == 1
    assert gets == ["signed-right-offer"]
    assert not old.exists()
    assert Path(db.placements()[0]["path"]).parent == root / "A" / "Acquisitions"
    with db._lock, db._connect() as sql:
        status = sql.execute(
            "SELECT status,artifact_id FROM acquisition_ledger "
            "WHERE acquisition_id LIKE 'canonical-intake:%'",
        ).fetchone()
    assert status == ("complete", "sha256:" + SHA)



@pytest.mark.parametrize("mutation", ["second_owner", "original_owner_rebound"])
def test_owner_registry_changed_after_final_hash_blocks_stock_sql_relink(
    rig, monkeypatch, mutation,
):
    """A completed staging Placement cannot be moved into a rebounded folder."""
    from cifereca.core import canonical_staging_stock as stock

    service, storage, root, old, gets = _legacy_placement(rig, monkeypatch)
    prior_placement = dict(storage.placements()[0])
    if mutation == "second_owner":
        storage.identity_put({
            "identity_id": "B", "directory_name": "B", "label": "B",
        })
        with storage._lock, storage._connect() as db:
            db.execute("DROP INDEX uq_identity_directory_name")

    real_verify = stock._path_verified
    mutated = []

    def verify_then_rebind(*args, **kwargs):
        verified = real_verify(*args, **kwargs)
        if (not mutated and Path(kwargs["path"]).parent.name == "Acquisitions"):
            with storage._lock, storage._connect() as db:
                if mutation == "second_owner":
                    db.execute(
                        "UPDATE identities SET directory_name='A' "
                        "WHERE identity_id='B'"
                    )
                else:
                    db.execute(
                        "UPDATE identities SET directory_name='rebound' "
                        "WHERE identity_id='A'"
                    )
            mutated.append(True)
        return verified

    monkeypatch.setattr(stock, "_path_verified", verify_then_rebind)
    outcome = synchronize_account_source(service, service.account)
    assert outcome["canonical_execution"]["failed"] == 1
    assert mutated == [True]
    assert storage.placements()[0] == prior_placement
    assert old.read_bytes() == BYTES
    assert gets == ["signed-right-offer"]  # no second supplier GET
    assert len(list((root / "A" / "Acquisitions").glob("*.pdf"))) == 1

    # The final file can be reused after repairing the registry. No staged
    # file is lost and no new authentication or remote delivery is needed.
    with storage._lock, storage._connect() as db:
        if mutation == "second_owner":
            db.execute("UPDATE identities SET directory_name='B' WHERE identity_id='B'")
        else:
            db.execute(
                "UPDATE identities SET directory_name='A' WHERE identity_id='A'"
            )
    monkeypatch.setattr(stock, "_path_verified", real_verify)
    restored = synchronize_account_source(service, service.account)
    assert restored["canonical_execution"]["recovered"] == 1
    assert gets == ["signed-right-offer"]
    assert Path(storage.placements()[0]["path"]).parent == root / "A" / "Acquisitions"
