"""#3171: revalidate live session ownership at the SourceReference commit.

Real SQLite transactions; synthetic provider rights only, zero network.
"""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core import canonical_sync_stage as stage
from cifereca.core.sync_coordination import synchronize_account_source
from cifereca.models import Account
from tests.test_core_p0_3171_canonical_sync_stage import rig, refs


def foreign(account):
    return replace(account, unique_id="another-borrower")


def change(service, kind):
    session, account = service.session, service.account
    if kind == "borrower_removed":
        session.accounts = []
    elif kind == "borrower_changed":
        session.accounts = [foreign(account)]
    elif kind == "borrower_duplicated":
        session.accounts.append(replace(account))
    elif kind == "borrower_unreadable":
        session.accounts = None
    elif kind == "provider_changed":
        session.provider_id = "another-provider"
    elif kind == "credential_changed":
        session.credential_id = "another-credential"
    elif kind == "session_duplicated":
        service._sessions["unexpected-registration"] = NS(
            provider_id=account.provider_id, credential_id=account.credential_id,
        )
    else:
        raise AssertionError(kind)


@pytest.mark.parametrize("kind", [
    "borrower_removed", "borrower_changed", "borrower_duplicated",
    "borrower_unreadable", "provider_changed", "credential_changed", "session_duplicated",
])
def test_mid_sql_session_change_rolls_back_whole_positive_batch(rig, kind):
    service, store, root = rig
    synchronize_account_source(service, service.account)
    before = refs(store)
    first = replace(service.snapshot.rights[0], evidence_ref="new-observed-proof")
    second = replace(first, edition_id="second-edition", right_id="second-purchase")
    service.snapshot = replace(service.snapshot, rights=(first, second))
    connection = store._connect()
    inserts = []
    def during_insert(sql):
        if sql.startswith("INSERT INTO source_references("):
            inserts.append(sql)
            change(service, kind)
    connection.set_trace_callback(during_insert)
    try:
        with pytest.raises(ValueError, match="canonical_rights_source_rebound"):
            synchronize_account_source(service, service.account)
    finally:
        connection.set_trace_callback(None)
    assert inserts  # Changed after admission and while SQL was in flight.
    assert not connection.in_transaction
    assert refs(store) == before  # Original proof/state unchanged, no second right.
    assert store.placements() == [] and store.artifacts() == []
    assert store.entitlements() == [] and store.acquisitions() == []
    assert service.read_calls == 2
    assert not root.exists()


def test_switch_between_read_and_transaction_is_rejected_before_insert(rig, monkeypatch):
    service, store, root = rig
    original = stage.persist_attested_source_references
    def switch(*args, **kwargs):
        change(service, "borrower_changed")
        return original(*args, **kwargs)
    monkeypatch.setattr(stage, "persist_attested_source_references", switch)
    with pytest.raises(ValueError, match="canonical_rights_source_rebound"):
        synchronize_account_source(service, service.account)
    assert refs(store) == ([], 0)
    assert not store._connect().in_transaction
    assert service.read_calls == 1 and not root.exists()


def test_switch_after_presence_check_never_hands_off_stale_snapshot(rig, monkeypatch):
    service, store, root = rig
    original = stage.plan_existing_verified_presence
    def switch(*args, **kwargs):
        plan = original(*args, **kwargs)
        change(service, "borrower_changed")
        return plan
    monkeypatch.setattr(stage, "plan_existing_verified_presence", switch)
    admitted = []
    with pytest.raises(ValueError, match="canonical_rights_source_rebound"):
        stage.observe_canonical_rights_in_sync(service, service.account, snapshot_sink=admitted.append)
    assert admitted == []
    # A committed under the valid borrower remains historical positive evidence;
    # changing session later cannot authorize delivery or erase that observation.
    assert len(refs(store)[0]) == 1 and refs(store)[1] == 0
    assert store.placements() == [] and not root.exists()


def test_valid_linked_accounts_remain_supported_and_second_sync_is_idempotent(rig):
    service, store, root = rig
    service.session.accounts.append(foreign(service.account))
    first = synchronize_account_source(service, service.account)
    assert first["canonical_rights"]["source_references_changed"] == 1
    before = refs(store)
    second = synchronize_account_source(service, service.account)
    assert second["canonical_rights"]["source_references_changed"] == 0
    assert refs(store) == before
    assert service.read_calls == 2 and not root.exists()
