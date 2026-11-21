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
from cifereca.plugin_api import RetryableContentDownload
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
        raise RetryableContentDownload("synthetic transfer failure")
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
        raise RetryableContentDownload("synthetic rebind")
    run(service, account, snap, root, lock_path, {source}, rebind)
    assert read_loan_download_retry(service.storage, source).failures == 0


def test_review_and_reset_one_paused_loan_without_provider_io(rig, monkeypatch):
    from types import SimpleNamespace
    from cifereca.core.canonical_sync_stage import persist_attested_source_references
    from cifereca.core.loan_download_retry_review import (
        review_loan_download_retries, reset_reviewed_loan_download_retry,
    )
    from cifereca.models import Loan
    service, account, source, snap, root, lock_path = loan_snapshot(rig)
    service.current_identity = SimpleNamespace(identity_id="A")
    service.last_loans = [
        Loan(id=source.right_id, provider="backend", medium="digital",
             record_id=source.edition_id, title="Exact synthetic book",
             source_provider=source.scope.provider_id,
             source_credential=source.scope.credential_id,
             source_account_id=source.scope.account_id, identity_id="A"),
    ]
    persist_attested_source_references(service.storage, snap)
    monkeypatch.setattr("cifereca.core.loan_download_retry_review.acquisition_lock_file",
                        lambda: lock_path)
    with InterProcessLock(lock_path) as lease:
        for _ in range(FAILURE_LIMIT):
            record_loan_download_failure(service.storage, source,
                                         reason="canonical_delivery_failed", shared_lock=lease)
    row, = review_loan_download_retries(service, identity_id="A")
    assert row.source == source and row.title == "Exact synthetic book"
    assert row.can_reset and row.retry.paused
    with pytest.raises(ValueError, match="scope_changed"):
        review_loan_download_retries(service, identity_id="B")
    reset_reviewed_loan_download_retry(service, identity_id="A", row=row)
    assert review_loan_download_retries(service, identity_id="A") == ()
    assert service.calls == 0
    with pytest.raises(ValueError, match="reset_changed"):
        reset_reviewed_loan_download_retry(service, identity_id="A", row=row)


def test_old_ledger_attention_without_marker_is_visible_and_cannot_reset(rig, monkeypatch):
    from types import SimpleNamespace
    from cifereca.core.canonical_sync_stage import persist_attested_source_references
    from cifereca.core.loan_download_retry_review import (
        review_loan_download_retries, reset_reviewed_loan_download_retry,
    )
    service, account, source, snap, root, lock_path = loan_snapshot(rig)
    service.current_identity = SimpleNamespace(identity_id="A")
    persist_attested_source_references(service.storage, snap)
    monkeypatch.setattr("cifereca.core.loan_download_retry_review.acquisition_lock_file",
                        lambda: lock_path)
    with InterProcessLock(lock_path) as lease:
        for _ in range(FAILURE_LIMIT):
            record_loan_download_failure(service.storage, source,
                                         reason="canonical_delivery_failed", shared_lock=lease)
    before, = review_loan_download_retries(service, identity_id="A")
    assert before.can_reset
    service.storage.acquisition_put({
        "acquisition_id": "synthetic-historic-adept",
        "identity_id": source.scope.identity_id,
        "provider_id": source.scope.provider_id,
        "credential_id": source.scope.credential_id,
        "account_id": source.scope.account_id,
        "item_id": source.edition_id,
        "status": "attention_required", "strategy": "loan_auto",
        "executor": "kobo-adept", "metadata": {"provider_loan_id": source.right_id},
    })
    assert not pending_loan_adept_attempt(service.storage, source)
    after, = review_loan_download_retries(service, identity_id="A")
    assert after.adept_hold and not after.can_reset
    with pytest.raises(ValueError, match="reset_changed"):
        reset_reviewed_loan_download_retry(service, identity_id="A", row=before)
    assert read_loan_download_retry(service.storage, source).paused


def test_owned_resource_pause_uses_same_queue_and_isolated_occurrence(rig, monkeypatch):
    from types import SimpleNamespace
    from cifereca.core.canonical_sync_stage import persist_attested_source_references
    from cifereca.core.loan_download_retry_review import (
        review_loan_download_retries, reset_reviewed_loan_download_retry,
    )

    service, account, source, snapshot, root, lock_path = rig
    other = replace(source, right_id="second-copy")
    other_row = replace(snapshot.rights[0], right_id=other.right_id)
    both = replace(snapshot, rights=(snapshot.rights[0], other_row))
    service.current_identity = SimpleNamespace(identity_id=source.scope.identity_id)
    persist_attested_source_references(service.storage, both)
    monkeypatch.setattr("cifereca.core.loan_download_retry_review.acquisition_lock_file",
                        lambda: lock_path)
    attempts = []
    def fail(key, offer):
        attempts.append((key, offer.delivery_offer_id))
        raise RetryableContentDownload("temporary transport error")

    for attempt in range(FAILURE_LIMIT):
        fresh = replace(both, rights=(
            replace(both.rights[0], delivery_offer_id=f"offer-{attempt}"), other_row,
        ))
        run(service, account, fresh, root, lock_path, {source}, fail)
    assert attempts == [(source, f"offer-{n}") for n in range(FAILURE_LIMIT)]
    queued, = review_loan_download_retries(service, identity_id="A")
    assert queued.source == source
    assert queued.can_reset
    assert read_loan_download_retry(service.storage, other).failures == 0

    result = run(service, account, both, root, lock_path, {source, other},
                 lambda key, row: fake_delivery(service, root)(key, row)
                 if key == other else pytest.fail("paused purchase fetched"))
    assert [(row.source, row.status, row.reason) for row in result] == [
        (source, "blocked", "download_paused"),
        (other, "downloaded", "canonical_delivery_committed"),
    ]
    reset_reviewed_loan_download_retry(service, identity_id="A", row=queued)
    assert read_loan_download_retry(service.storage, source).failures == 0
    assert review_loan_download_retries(service, identity_id="A") == ()


@pytest.mark.parametrize("exception", [OSError, ValueError])
def test_unqualified_failure_does_not_consume_a_retry(rig, exception):
    service, account, source, snapshot, root, lock_path = loan_snapshot(rig)
    def refuse(*_):
        raise exception("synthetic preflight failure or ambiguous runtime")
    for _ in range(FAILURE_LIMIT):
        outcome, = run(service, account, snapshot, root, lock_path, {source}, refuse)
        assert outcome.reason == "canonical_delivery_failed"
    assert read_loan_download_retry(service.storage, source).failures == 0


def test_pause_does_not_gate_a_separately_confirmed_owned_download(rig):
    from cifereca.core.convergence_execution import execute_admitted_convergence

    service, account, source, snapshot, root, lock_path = rig
    observed, = snapshot.rights
    attempted = []
    def fail(key, _offer):
        attempted.append(key)
        raise RetryableContentDownload("temporary transfer failure")

    with InterProcessLock(lock_path) as lease:
        for _ in range(FAILURE_LIMIT):
            record_loan_download_failure(service.storage, source,
                                         reason="canonical_delivery_failed",
                                         shared_lock=lease)
        result, = execute_admitted_convergence(
            service, account, service._sessions[service._session_key(
                account.provider_id, account.credential_id,
            )], snapshot, admitted_epoch=service._source_context_epoch,
            shared_lock=lease, library_root=root,
            persisted_consent=frozenset({source}),
            consent_still_current=lambda key: key == source, deliver=fail,
            publication_guard=lambda *_: True, explicitly_confirmed_once=True,
            explicit_offer_id=observed.delivery_offer_id,
        )
    assert attempted == [source]
    assert result.reason == "canonical_delivery_failed"
    assert read_loan_download_retry(service.storage, source).paused


@pytest.mark.parametrize("kind", ["loan", "purchase"])
def test_local_adept_profile_refusal_is_visible_before_any_pause(rig, kind):
    from types import SimpleNamespace
    from cifereca.core.adept_runtime_preflight import AdeptProfileUnavailable
    from cifereca.core.canonical_sync_stage import persist_attested_source_references
    from cifereca.core.loan_download_retry_review import review_loan_download_retries

    service, account, source, snapshot, root, lock_path = (
        loan_snapshot(rig) if kind == "loan" else rig
    )
    service.current_identity = SimpleNamespace(identity_id=source.scope.identity_id)
    persist_attested_source_references(service.storage, snapshot)

    def refuse_profile(*_):
        raise AdeptProfileUnavailable("canonical_adept_preflight_unavailable")

    outcome, = run(service, account, snapshot, root, lock_path, {source}, refuse_profile)
    assert outcome.reason == "canonical_adept_profile_attention"
    row, = review_loan_download_retries(service, identity_id=source.scope.identity_id)
    assert row.source == source
    assert row.profile_refused_at > 0
    assert row.retry.failures == 0
    assert not row.adept_hold and not row.can_reset

    outcome, = run(service, account, snapshot, root, lock_path, {source},
                   fake_delivery(service, root))
    assert outcome.status == "downloaded"
    assert review_loan_download_retries(service, identity_id=source.scope.identity_id) == ()
