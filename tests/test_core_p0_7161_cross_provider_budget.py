"""#7161: exact Core retry budget across providers and eligible source kinds.

Synthetic transports only. A paused content transfer is not a grant to
replay a mutation, and a completed local receipt takes precedence over GET.
"""
from dataclasses import replace

import pytest

from cifereca.locking import InterProcessLock
from cifereca.models import Account
from cifereca.plugin_api import RetryableContentDownload
from cifereca.storage import Storage
from cifereca.core import convergence_execution as engine
from cifereca.core.loan_download_retry import (
    FAILURE_LIMIT, record_loan_download_failure, read_loan_download_retry,
)
from tests.test_core_p0_3171_convergence_execution import rig, run, fake_delivery
from tests.test_core_p0_3171_atomic_publication_v14 import (
    new_source, register, rows,
)
from tests.test_core_p0_3171_crash_recovery_v14 import (
    force_failure_once, journal,
)
from tests.test_core_p1_7161_loan_download_retry import loan_snapshot


def _second_provider(service):
    account = Account(
        identity_id="B", provider_id="seller-b",
        credential_id="card-B", unique_id="buyer-B",
    )
    register(service.storage, "B", "seller-b", "card-B", account="buyer-B")
    service.accounts.append(account)
    from types import SimpleNamespace as NS
    service._sessions[service._session_key(account.provider_id, account.credential_id)] = NS(
        provider_id=account.provider_id, credential_id=account.credential_id,
        connected=True, _closed=False, accounts=[account],
    )
    source, snapshot = new_source(
        "B", provider="seller-b", credential="card-B", account="buyer-B",
        kind="purchase", branch="purchases", right="owned-1",
    )
    snapshot = replace(snapshot, rights=(replace(
        snapshot.rights[0], delivery="available",
        delivery_offer_id="independent-offer-B",
    ),))
    return account, source, snapshot


def test_two_providers_loan_and_purchase_keep_independent_retry_cycles(rig):
    service, account_a, source_a, snap_a, root, lock_path = loan_snapshot(rig)
    account_b, source_b, snap_b = _second_provider(service)
    calls = []

    def fail_a(key, right):
        calls.append(("A", key, right.delivery_offer_id))
        raise RetryableContentDownload("synthetic redacted transfer error")

    def fail_b(key, right):
        calls.append(("B", key, right.delivery_offer_id))
        raise RetryableContentDownload("synthetic redacted transfer error")

    for index in range(FAILURE_LIMIT):
        rotated = replace(snap_a, rights=(replace(
            snap_a.rights[0], delivery_offer_id=f"fresh-A-{index}",
        ),))
        result, = run(service, account_a, rotated, root, lock_path, {source_a}, fail_a)
        assert result.status == "failed"
        assert read_loan_download_retry(service.storage, source_a).failures == index + 1
    assert len(calls) == FAILURE_LIMIT
    blocked, = run(service, account_a, snap_a, root, lock_path, {source_a}, fail_a)
    assert (blocked.status, blocked.reason) == ("blocked", "loan_download_paused")
    assert len(calls) == FAILURE_LIMIT

    one, = run(service, account_b, snap_b, root, lock_path, {source_b}, fail_b)
    assert one.status == "failed"
    assert read_loan_download_retry(service.storage, source_b).failures == 1
    assert read_loan_download_retry(service.storage, source_a).paused
    reopened = Storage(service.storage.path)
    try:
        assert read_loan_download_retry(reopened, source_a).paused
        assert read_loan_download_retry(reopened, source_b).failures == 1
    finally:
        reopened.close()

    published, = run(
        service, account_b, snap_b, root, lock_path, {source_b},
        fake_delivery(service, root, profile="B"),
    )
    assert (published.status, published.reason) == (
        "downloaded", "canonical_delivery_committed",
    )
    assert read_loan_download_retry(service.storage, source_b).failures == 0
    assert read_loan_download_retry(service.storage, source_a).paused
    assert rows(service.storage) == (1, 1, 1, 1)
    assert service.storage.placement_for_path(root / "B" / "book.pdf")["identity_id"] == "B"
    assert len(calls) == FAILURE_LIMIT + 1
    assert run(
        service, account_b, snap_b, root, lock_path, {source_b},
        lambda *_: pytest.fail("published B must prevent another supplier GET"),
    )[0].status == "present_attested"


@pytest.mark.parametrize("kind", ["loan", "purchase"])
def test_existing_completed_receipt_recovers_during_pause_without_new_get(
    rig, monkeypatch, kind,
):
    if kind == "loan":
        service, account, source, snapshot, root, lock_path = loan_snapshot(rig)
    else:
        service, account, source, snapshot, root, lock_path = rig

    # One completed content delivery leaves a FULL durable checkpoint, but B
    # fails before committing. This is not a second supplier permission.
    original = force_failure_once(monkeypatch)
    first, = run(
        service, account, snapshot, root, lock_path, {source},
        fake_delivery(service, root),
    )
    monkeypatch.setattr(engine, "publish_completed_acquisition", original)
    assert first.status == "failed" and service.calls == 1
    assert rows(service.storage) == (0, 0, 0, 0)
    receipt, = journal(service.storage)
    assert receipt[:2] == ("canonical_receipt_staged", None)
    with InterProcessLock(lock_path) as lease:
        for _ in range(FAILURE_LIMIT):
            record_loan_download_failure(
                service.storage, source, reason="canonical_delivery_failed",
                shared_lock=lease,
            )
    assert read_loan_download_retry(service.storage, source).paused
    assert run(
        service, account, snapshot, root, lock_path, {source},
        lambda *_: pytest.fail("pause must not reissue GET when final receipt exists"),
    )[0].status == "recovered"
    assert service.calls == 1
    assert rows(service.storage) == (1, 1, 1, 1)
    assert read_loan_download_retry(service.storage, source).failures == 0
    assert run(
        service, account, snapshot, root, lock_path, {source},
        lambda *_: pytest.fail("B now attested: no duplicate GET"),
    )[0].status == "present_attested"
