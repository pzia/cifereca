"""P0 #3719/#3185: a same-SHA completed journal is not provider proof.

Corrupt/imported SQLite may keep deterministic OriginLink/receipt identifiers
even when the SourceReference's attestation or the borrower's connection_id
was rebound. A preview MUST block and transaction MUST leave files untouched.
"""
import json

import pytest

from cifereca.core.canonical_dedup_reference_plan import (
    plan_verified_canonical_reference_migration,
)
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, rows, store
from tests.test_core_p0_3719_receipt_relink import rig, journal, relink, saved


@pytest.mark.parametrize("corruption", ("source_unattested", "account_rebound"))
def test_receipt_relink_requires_attested_source_and_exact_enabled_borrower(
    rig, corruption,
):
    storage, root, paths, pubs, source, _lock = rig
    record_id = journal(rig)
    before_journal = saved(rig, record_id)
    before_rows = rows(storage)
    with storage._lock, storage._connect() as db:
        if corruption == "source_unattested":
            changed = db.execute(
                "UPDATE source_references SET evidence_kind='unverified' "
                "WHERE identity_id='A' AND provider_id='seller-b' "
                "AND credential_id='card-b' AND account_id='buyer-b'",
            )
        else:
            other = db.execute(
                "SELECT connection_id FROM provider_connections "
                "WHERE provider_id='seller-a' AND credential_id='card-a'",
            ).fetchone()[0]
            changed = db.execute(
                "UPDATE provider_accounts SET connection_id=? "
                "WHERE identity_id='A' AND provider_id='seller-b' "
                "AND credential_id='card-b' AND account_id='buyer-b'",
                (other,),
            )
        assert changed.rowcount == 1
        proof_before = db.execute(
            "SELECT origin_link_id,source_reference_id,artifact_id,evidence_ref "
            "FROM origin_links ORDER BY origin_link_id",
        ).fetchall()
    plan = plan_verified_canonical_reference_migration(
        storage, library_root=root,
        candidate_placement_id=pubs[1].placement_id,
        keeper_placement_id=pubs[0].placement_id,
    )
    assert plan.status == "blocked"
    assert plan.reason == "unverified_references"
    assert plan.completed_receipt_candidates == 0
    assert plan.unverified_references >= 1
    with pytest.raises(
        ValueError, match="canonical_receipt_relink_origin_not_published",
    ):
        relink(rig)
    assert saved(rig, record_id) == before_journal
    assert rows(storage) == before_rows
    assert all(path.read_bytes() == BYTES for path in paths)
    assert storage.placement_get(pubs[1].placement_id)["state"] == "present"
    with storage._lock, storage._connect() as db:
        assert db.execute(
            "SELECT origin_link_id,source_reference_id,artifact_id,evidence_ref "
            "FROM origin_links ORDER BY origin_link_id",
        ).fetchall() == proof_before
        if corruption == "source_unattested":
            fixed = db.execute(
                "UPDATE source_references SET evidence_kind='provider_response' "
                "WHERE identity_id='A' AND provider_id='seller-b' "
                "AND credential_id='card-b' AND account_id='buyer-b'",
            )
        else:
            correct = db.execute(
                "SELECT connection_id FROM provider_connections "
                "WHERE provider_id='seller-b' AND credential_id='card-b'",
            ).fetchone()[0]
            fixed = db.execute(
                "UPDATE provider_accounts SET connection_id=? "
                "WHERE identity_id='A' AND provider_id='seller-b' "
                "AND credential_id='card-b' AND account_id='buyer-b'",
                (correct,),
            )
        assert fixed.rowcount == 1
    repaired_plan = plan_verified_canonical_reference_migration(
        storage, library_root=root,
        candidate_placement_id=pubs[1].placement_id,
        keeper_placement_id=pubs[0].placement_id,
    )
    assert repaired_plan.status == "review_required"
    assert repaired_plan.completed_receipt_candidates == 1
    assert relink(rig) == 1
    assert json.loads(saved(rig, record_id)[2])["path"] == str(paths[0])
    assert rows(storage) == before_rows
    assert all(path.read_bytes() == BYTES for path in paths)
