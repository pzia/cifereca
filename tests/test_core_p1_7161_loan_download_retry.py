"""#7161: bounded attempts on exact existing loans, with real SQLite."""
from dataclasses import replace

import pytest

from cifereca.core.canonical_adept_attempt import (
    begin_loan_adept_attempt, pending_loan_adept_attempt,
)
from cifereca.core.loan_download_retry import (
    FAILURE_LIMIT, clear_loan_download_retry, read_loan_download_retry,
    record_loan_download_failure,
)
from cifereca.locking import InterProcessLock
from cifereca.storage import Storage
from tests.test_core_p0_3171_convergence_execution import rig, run, fake_delivery
from tests.test_core_p0_3171_atomic_publication_v14 import new_source, rows


def loan_snapshot(rig):
    service, account, _, _, root, lock_path = rig
    source, snap = new_source(
        "A", provider="seller-a", credential="card-A", account="buyer-A",
        branch="loans", right="loan-1", kind="loan",
    )
    snap = replace(snap, rights=(replace(
        snap.rights[0], delivery="available", delivery_offer_id="offer-1",
    ),))
    return service, account, source, snap, root, lock_path


def test_three_failures_pause_exact_loan_across_store_reopen(rig):
    service, account, source, snap, root, lock_path = loan_snapshot(rig)
    calls = []
    def fail(*args):
        calls.append(args[0])
        raise OSError("synthetic delivery failure")
    for n in range(1, FAILURE_LIMIT + 1):
        result, = run(service, account, snap, root, lock_path, {source}, fail)
        assert result.status == "failed"
        assert result.reason == ("loan_download_paused" if n == FAILURE_LIMIT
                                 else "canonical_delivery_failed")
        retry = read_loan_download_retry(service.storage, source)
        assert retry.failures == n
        assert retry.last_reason == "canonical_delivery_failed"
        assert retry.paused is (n == FAILURE_LIMIT)
    reopened = Storage(service.storage.path)
    try:
        assert read_loan_download_retry(reopened, source).paused
    finally:
        reopened.close()
    rotated = replace(snap, rights=(replace(snap.rights[0],
                                           delivery_offer_id="new-offer"),))
    result, = run(service, account, rotated, root, lock_path, {source}, fail)
    assert (result.status, result.reason) == ("blocked", "loan_download_paused")
    assert calls == [source] * FAILURE_LIMIT
    assert rows(service.storage) == (0, 0, 0, 0)
    assert read_loan_download_retry(service.storage, replace(source, right_id="new-loan")).failures == 0
    assert read_loan_download_retry(service.storage, replace(
        source, scope=replace(source.scope, account_id="another-account"))).failures == 0


def test_success_clears_only_exact_loan_budget(rig):
    service, account, source, snap, root, lock_path = loan_snapshot(rig)
    other = replace(source, right_id="loan-2")
    with InterProcessLock(lock_path) as lease:
        for key in (source, other):
            record_loan_download_failure(service.storage, key,
                                         reason="canonical_delivery_failed",
                                         shared_lock=lease)
        # Reset is not a provider mutation, an ADEPT replay permit or a B write.
        clear_loan_download_retry(service.storage, source, shared_lock=lease)
        record_loan_download_failure(service.storage, source,
                                     reason="canonical_delivery_failed", shared_lock=lease)
    result, = run(service, account, snap, root, lock_path, {source},
                  fake_delivery(service, root))
    assert result.status == "downloaded"
    assert read_loan_download_retry(service.storage, source).failures == 0
    assert read_loan_download_retry(service.storage, other).failures == 1
    with InterProcessLock(lock_path) as lease:
        for _ in range(FAILURE_LIMIT):
            record_loan_download_failure(service.storage, source,
                                         reason="canonical_delivery_failed", shared_lock=lease)
    result, = run(service, account, snap, root, lock_path, {source},
                  lambda *_: pytest.fail("verified B never redownloads"))
    assert result.status == "present_attested"
    assert read_loan_download_retry(service.storage, source).failures == 0


def test_reset_does_not_unlock_uncertain_adept_fulfillment(rig):
    service, account, source, snap, root, lock_path = loan_snapshot(rig)
    with InterProcessLock(lock_path) as lease:
        begin_loan_adept_attempt(service.storage, source=source, offer_id="offer-1",
                                 shared_lock=lease)
        record_loan_download_failure(service.storage, source,
                                     reason="canonical_delivery_failed", shared_lock=lease)
        clear_loan_download_retry(service.storage, source, shared_lock=lease)
    assert pending_loan_adept_attempt(service.storage, source)
    result, = run(service, account, snap, root, lock_path, {source},
                  lambda *_: pytest.fail("uncertain fulfillment never replays"))
    assert (result.status, result.reason) == ("blocked", "persisted_adept_attention")
    assert read_loan_download_retry(service.storage, source).failures == 0


def test_cancel_and_revocation_do_not_consume_budget(rig):
    from cifereca.plugin_api.errors import Cancelled
    service, account, source, snap, root, lock_path = loan_snapshot(rig)
    def cancel(*_):
        raise Cancelled("synthetic")
    result, = run(service, account, snap, root, lock_path, {source}, cancel)
    assert result.status == "cancelled"
    assert read_loan_download_retry(service.storage, source).failures == 0
    def rebind(*_):
        service.enabled = False
        raise OSError("synthetic rebind")
    run(service, account, snap, root, lock_path, {source}, rebind)
    assert read_loan_download_retry(service.storage, source).failures == 0
