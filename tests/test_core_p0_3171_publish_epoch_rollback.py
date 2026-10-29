"""#3171: Core rebind DURING canonical post-GET B SQL causes full rollback.

SQLite triggers mutate the in-memory Core account/session/epoch after the
first persistent write. The staged completed GET remains in the journal, but
NO Artifact, SourceReference, Placement or OriginLink may commit for stale A.
A later admitted Sync can then publish from the *same* final receipt sans GET.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.core.convergence_execution import execute_admitted_convergence
from tests.test_core_p0_3171_convergence_execution import (
    rig, run, fake_delivery,
)
from tests.test_core_p0_3171_crash_recovery_v14 import journal
from tests.test_core_p0_3171_atomic_publication_v14 import rows


@pytest.mark.parametrize("mutation,table", [
    ("epoch", "artifacts"),
    ("session", "origin_links"),
    ("account", "placements"),
])
def test_mid_publication_core_rebind_rolls_back_all_B_but_keeps_durable_receipt(
    rig, mutation, table,
):
    service, account, source, snapshot, root, lock_path = rig
    original_session = service._sessions[("seller-a", "card-A")]
    original_account = account

    def invalidate():
        if mutation == "epoch":
            service._source_context_epoch += 1
        elif mutation == "session":
            service._sessions[("seller-a", "card-A")] = NS(
                provider_id="seller-a", credential_id="card-A",
                connected=True, _closed=False,
            )
        else:
            from cifereca.models import Account
            service.accounts[0] = Account(
                identity_id="A", provider_id="seller-a",
                credential_id="card-A", unique_id="buyer-A",
            )
        return 0

    with service.storage._lock, service.storage._connect() as db:
        db.create_function("mutate_sql_publication_context", 0, invalidate)
        db.execute(
            f"CREATE TRIGGER test_publishing_epoch AFTER INSERT ON {table} "
            "BEGIN SELECT mutate_sql_publication_context(); END"
        )
    try:
        first = run(
            service, account, snapshot, root, lock_path, {source},
            fake_delivery(service, root),
        )
        assert first[0].status == "failed"
        assert service.calls == 1
        assert rows(service.storage) == (0, 0, 0, 0)
        recorded, = journal(service.storage)
        assert recorded[0] == "canonical_receipt_staged"
    finally:
        with service.storage._lock, service.storage._connect() as db:
            db.execute("DROP TRIGGER test_publishing_epoch")
            db.create_function("mutate_sql_publication_context", 0, None)
        service.accounts[0] = original_account
        service._sessions[("seller-a", "card-A")] = original_session
        service._source_context_epoch += 1

    second = run(
        service, account, snapshot, root, lock_path, {source},
        lambda *_: pytest.fail("durable complete receipt must avoid second GET"),
    )
    assert second[0].status == "recovered"
    assert second[0].reason == "canonical_delivery_recovered"
    assert service.calls == 1
    assert rows(service.storage) == (1, 1, 1, 1)
    assert journal(service.storage)[0][0] == "complete"


def test_failed_publisher_context_guard_never_partial_commits_direct_write(
    rig,
):
    service, account, source, snapshot, root, lock_path = rig
    from cifereca.core.canonical_publication import publish_completed_acquisition
    from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, SHA
    path = root / "A" / "book.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(BYTES)
    checks = []

    def current():
        checks.append(1)
        return len(checks) == 1

    with pytest.raises(ValueError, match="canonical_publisher_context_rebound"):
        publish_completed_acquisition(
            service.storage, library_root=root, path=path,
            source=source, snapshot=snapshot, final_sha256=SHA,
            delivery_receipt_ref="synthetic-independent-receipt",
            context_current=current,
        )
    assert len(checks) == 2
    assert rows(service.storage) == (0, 0, 0, 0)
    assert path.read_bytes() == BYTES
