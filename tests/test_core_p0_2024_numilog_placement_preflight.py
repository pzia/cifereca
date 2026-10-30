"""P0 #2024: preflight must see the same placement-only conflicts as the lock.

A prior manual/imported placement with no acquisition_ledger row must never
be mistaken for an acquirable fresh Numilog edition. Evidence is local,
one-purchase scoped and redacted; no deletion, SQL repair or vendor GET.
"""
from __future__ import annotations

import json

import pytest

from cifereca.core.numilog_materialization_startup import execute
from tests.test_core_p0_numilog_purchase_preflight import _rig, _check
from tests.test_core_p0_numilog_auto_materialization import _setup, _authorize
from tests.test_core_p0_1862_numilog_store_mirror import _row


@pytest.mark.parametrize("state", ["present", "missing"])
@pytest.mark.parametrize("owner", ["correct", "other"])
def test_placement_without_ledger_is_reconciliation_not_download_candidate(
    tmp_path, state, owner,
):
    service, account, session, calls, item, entitlement, broker, sid = _rig(tmp_path)
    try:
        assert service.storage.numilog_purchase_has_local_conflict(
            credential_id=account.credential_id, item_id=item,
        ) is False
        path = tmp_path / f"PRIVATE_{owner}_{state}.epub"
        path.write_bytes(b"NOT_A_REAL_EPUB_AND_NOT_A_DOWNLOAD")
        service.storage.placement_put({
            "provider_id": "numilog",
            "identity_id": account.identity_id if owner == "correct" else "alex",
            "source_item_id": item, "path": str(path), "state": state,
            "metadata": {"private_url": "PRIVATE_SIGNED_LINK"},
        })
        evidence = service.storage.numilog_purchase_preflight_evidence(
            identity_id=account.identity_id, credential_id=account.credential_id,
            account_id=account.unique_id, item_id=item,
        )
        assert evidence["acquisitions"] == []
        assert evidence["placement_conflict"] is True
        assert service.storage.numilog_purchase_has_local_conflict(
            credential_id=account.credential_id, item_id=item,
        ) is True
        _check(broker.preflight_numilog_purchase(sid, item), "needs_reconciliation")
        assert calls == []
        assert "PRIVATE_SIGNED_LINK" not in repr(evidence)
        assert "PRIVATE_SIGNED_LINK" not in repr(broker.preflight_numilog_purchase(sid, item))
    finally:
        service.close()


def test_preflight_sql_queries_right_ledger_and_placement_in_one_snapshot(tmp_path):
    service, account, session, calls, item, entitlement, broker, sid = _rig(tmp_path)
    try:
        sql = []
        # _lock is a non-reentrant threading.Lock. Holding it while
        # calling the broker deadlocks when the broker enters Storage again.
        # _connect() is thread-local: attach the read-only SQL trace to the
        # same connection, but let the production method own its lock.
        db = service.storage._connect()
        db.set_trace_callback(sql.append)
        try:
            _check(
                broker.preflight_numilog_purchase(sid, item),
                "candidate_unverified", candidate=True,
            )
        finally:
            db.set_trace_callback(None)
        lookup = [s for s in sql if "FROM placements" in s
                  and "source_item_id=" in s]
        assert len(lookup) == 1
        assert "provider_id='numilog'" in lookup[0]
        assert "LIMIT 1" in lookup[0]
        assert len([s for s in sql if "FROM acquisition_ledger" in s
                    and "credential_id=" in s]) == 1
        assert not any(s.lstrip().startswith(("DELETE ", "UPDATE ", "INSERT "))
                       for s in sql)
        assert calls == []
    finally:
        service.close()


def test_startup_recovers_conflicting_same_owner_placement_without_overwrite(
    tmp_path,
):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        row = _row(account.credential_id, 0)
        session.library = lambda: [row]
        requests = []
        def unavailable(*_a, **_kw):
            requests.append(True)
            raise RuntimeError("synthetic delivery unavailable")
        session.download_direct = unavailable
        target = tmp_path / "PRIVATE_OLD_PLACEMENT.epub"
        target.write_bytes(b"FAKE_UNVERIFIED_FILE")
        service.storage.placement_put({
            "provider_id": "numilog", "identity_id": account.identity_id,
            "source_item_id": row.record_id, "path": str(target),
            "state": "present",
        })
        first = execute(broker, account.identity_id, "startup")
        assert first.facts["attempted"] == 1
        assert first.facts["downloaded"] == 0
        assert not first.ok and first.facts["status"] == "failed"
        assert first.facts["skipped"] == 0
        assert first.facts["blocked"] == 0
        assert first.facts["failed"] == 1
        assert requests == [True]
        assert target.read_bytes() == b"FAKE_UNVERIFIED_FILE"
        assert service.storage.acquisitions(provider_id="numilog") == []
        assert calls == []
        second = execute(broker, account.identity_id, "startup")
        assert second.facts["status"] == "not_due"
        assert second.facts["attempted"] == second.facts["downloaded"] == 0
    finally:
        service.close()
