"""#3171: Core session/epoch changes inside SQLite transaction roll back ALL A.

Inject a Python callback through a SQLite trigger after the first positive
SourceReference insert. No provider network, no old-right inactivation and no
OriginLink/Placement mutation may escape the aborted BEGIN IMMEDIATE.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.models import Account
from tests.test_core_p0_3171_canonical_sync_stage import (
    rig, refs,
)
from cifereca.core.sync_coordination import synchronize_account_source


@pytest.mark.parametrize("mutation", ["epoch", "registry_object", "session_swap"])
def test_in_flight_registry_rebind_rolls_back_whole_positive_A_batch(
    rig, mutation,
):
    service, db, root = rig
    second = replace(
        service.snapshot.rights[0],
        edition_id="second-edition", right_id="second-right",
    )
    service.snapshot = replace(
        service.snapshot, rights=service.snapshot.rights + (second,),
    )
    original_session = service.session
    original_account = service.account

    def mutate():
        if mutation == "epoch":
            service._account_context_epoch += 1
        elif mutation == "registry_object":
            service.accounts[0] = Account(
                identity_id="A", provider_id="seller-a",
                credential_id="card-a", unique_id="buyer-a",
            )
        else:
            from types import SimpleNamespace
            service._sessions[service._session_key("seller-a", "card-a")] = (
                SimpleNamespace(
                    provider_id="seller-a", credential_id="card-a",
                    connected=True, _closed=False,
                )
            )
        return 0

    with db._lock, db._connect() as conn:
        conn.create_function("synthetic_rebind_context", 0, mutate)
        conn.execute(
            "CREATE TRIGGER test_rebind_first_right AFTER INSERT ON "
            "source_references WHEN NEW.edition_id='edition' BEGIN "
            "SELECT synthetic_rebind_context(); END"
        )
    try:
        with pytest.raises(ValueError, match="canonical_rights_source_rebound"):
            synchronize_account_source(service, service.account)
        assert service.read_calls == 1
        assert refs(db) == ([], 0)
        assert db.placements() == [] and db.entitlements() == []
        assert db.acquisitions() == []
    finally:
        with db._lock, db._connect() as conn:
            conn.execute("DROP TRIGGER test_rebind_first_right")
            conn.create_function("synthetic_rebind_context", 0, None)
        service.accounts[0] = original_account
        service._sessions[service._session_key("seller-a", "card-a")] = original_session
        service._account_context_epoch += 1

    # The next correctly bound Sync may persist the entire source in one
    # transaction, rather than leaving a partial first row behind.
    completed = synchronize_account_source(service, service.account)
    assert completed["canonical_rights"]["source_references_changed"] == 2
    assert service.read_calls == 2
    assert len(refs(db)[0]) == 2
    assert refs(db)[1] == 0


def test_epoch_rebind_while_refreshing_existing_A_cannot_commit_partial_update(
    rig,
):
    service, db, root = rig
    assert synchronize_account_source(
        service, service.account,
    )["canonical_rights"]["source_references_changed"] == 1
    before = refs(db)
    observed = replace(
        service.snapshot.rights[0], evidence_ref="fresh-provider-evidence",
        observed_at="2026-10-28T12:00:00+00:00",
    )
    service.snapshot = replace(service.snapshot, rights=(observed,))

    def change_epoch():
        service._account_context_epoch += 1
        return 0

    with db._lock, db._connect() as conn:
        conn.create_function("mutate_epoch_after_update", 0, change_epoch)
        conn.execute(
            "CREATE TRIGGER test_rebind_existing_right AFTER UPDATE ON "
            "source_references BEGIN SELECT mutate_epoch_after_update(); END"
        )
    try:
        with pytest.raises(ValueError, match="canonical_rights_source_rebound"):
            synchronize_account_source(service, service.account)
        assert refs(db) == before
        with db._lock, db._connect() as conn:
            evidence = conn.execute(
                "SELECT evidence_ref FROM source_references"
            ).fetchone()[0]
        assert evidence != "fresh-provider-evidence"
    finally:
        with db._lock, db._connect() as conn:
            conn.execute("DROP TRIGGER test_rebind_existing_right")
            conn.create_function("mutate_epoch_after_update", 0, None)
