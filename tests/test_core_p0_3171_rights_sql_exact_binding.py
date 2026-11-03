"""P0 #3171: typed private inventory needs exact SQLite borrower binding.

Disposable v14 database; all provider I/O is synthetic. An active credential
does not prove that the formerly linked upstream account still belongs to it.
"""
from dataclasses import replace

import pytest

from cifereca.core.rights_snapshot_read import read_account_rights_snapshot
from tests.test_core_p0_3171_canonical_sync_stage import rig


@pytest.mark.parametrize("change", ["deleted", "reassigned"])
def test_sqlite_borrower_removed_or_reassigned_before_read_stops_io(rig, change):
    service, storage, _root = rig
    assert storage.provider_scope_account_bound(
        identity_id="A", provider_id="seller-a",
        credential_id="card-a", account_id="buyer-a",
    )
    with storage._lock, storage._connect() as db:
        if change == "deleted":
            db.execute(
                "DELETE FROM provider_accounts WHERE identity_id='A' "
                "AND provider_id='seller-a' AND credential_id='card-a' "
                "AND account_id='buyer-a'",
            )
        else:
            db.execute(
                "UPDATE provider_accounts SET account_id='other-borrower' "
                "WHERE identity_id='A' AND provider_id='seller-a' "
                "AND credential_id='card-a' AND account_id='buyer-a'",
            )
    assert storage.identity_for_connection("seller-a", "card-a") == "A"
    service.session.rights_observation_snapshot = lambda _account: pytest.fail(
        "never issue private inventory request for removed borrower"
    )
    result = read_account_rights_snapshot(service, service.account)
    assert result.status == "failed"
    assert result.rights == ()
    assert result.coverage == "unknown"
    assert service.read_calls == 0


def test_sqlite_borrower_rebound_during_inventory_discards_positive_rows(rig):
    service, storage, _root = rig
    previous = service.snapshot

    def changed(_account):
        service.read_calls += 1
        with storage._lock, storage._connect() as db:
            db.execute(
                "UPDATE provider_accounts SET account_id='other-borrower' "
                "WHERE identity_id='A' AND provider_id='seller-a' "
                "AND credential_id='card-a' AND account_id='buyer-a'",
            )
        return replace(previous, status="partial")

    service.session.rights_observation_snapshot = changed
    result = read_account_rights_snapshot(service, service.account)
    assert service.read_calls == 1
    assert result.status == "failed" and result.rights == ()
    assert result.coverage == "unknown"


@pytest.mark.parametrize("phase", ("before", "during", "between_pages", "lookup_failed"))
def test_source_disable_revokes_private_inventory_lease_without_positive_A(
    rig, phase,
):
    """Disabled sources cannot fetch a first/next page or keep stale positives."""
    service, _storage, _root = rig
    original = service.snapshot
    if phase == "before":
        service.enabled = False
    elif phase == "lookup_failed":
        service.sync_source_enabled = lambda _: (_ for _ in ()).throw(
            OSError("synthetic source-state unavailable")
        )
    else:
        def read(_account, *, resume_cursor=""):
            if resume_cursor:
                pytest.fail("source disabled after first page: no next GET")
            service.read_calls += 1
            service.enabled = False
            return replace(
                original, resume_cursor="synthetic-next"
                if phase == "between_pages" else "",
            )
        service.session.rights_observation_snapshot = read

    if phase in ("before", "lookup_failed"):
        service.session.rights_observation_snapshot = (
            lambda _account: pytest.fail("disabled source: no first private GET")
        )
    result = read_account_rights_snapshot(service, service.account)
    assert result.status == "failed" and result.rights == ()
    assert result.coverage == "unknown"
    assert service.read_calls == (1 if phase in ("during", "between_pages") else 0)
