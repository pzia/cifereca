"""#7161: one credential may legitimately expose distinct borrower accounts.

A retry fence is per exact borrower/right, never credential-wide. Both the
Core pipeline and the operator queue must retain the account distinction.
"""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.models import Account
from cifereca.plugin_api import RetryableContentDownload
from cifereca.core.canonical_sync_stage import persist_attested_source_references
from cifereca.core.loan_download_retry import (
    FAILURE_LIMIT, read_loan_download_retry,
)
from cifereca.core.loan_download_retry_review import (
    review_loan_download_retries, reset_reviewed_loan_download_retry,
)
from tests.test_core_p0_3171_atomic_publication_v14 import new_source, rows
from tests.test_core_p0_3171_convergence_execution import rig, run, fake_delivery
from tests.test_core_p1_7161_loan_download_retry import loan_snapshot


def test_same_credential_distinct_borrowers_never_share_budget_or_B(
    rig, monkeypatch,
):
    service, borrower_a, source_a, snapshot_a, root, lock_path = loan_snapshot(rig)
    borrower_b = Account(
        identity_id="A", provider_id="seller-a",
        credential_id="card-A", unique_id="buyer-B",
    )
    storage = service.storage
    with storage._lock, storage._connect() as db:
        connection, = db.execute(
            "SELECT connection_id FROM provider_connections WHERE "
            "identity_id='A' AND provider_id='seller-a' AND credential_id='card-A'"
        ).fetchone()
    storage.provider_account_put({
        "connection_id": connection, "identity_id": "A",
        "provider_id": "seller-a", "credential_id": "card-A",
        "account_id": "buyer-B", "label": "Synthetic second borrower",
    })
    service.accounts.append(borrower_b)
    session = service._sessions[("seller-a", "card-A")]
    session.accounts = [borrower_a, borrower_b]
    source_b, snapshot_b = new_source(
        "A", provider="seller-a", credential="card-A", account="buyer-B",
        branch="loans", edition=source_a.edition_id,
        right=source_a.right_id, kind="loan",
    )
    snapshot_b = replace(snapshot_b, rights=(replace(
        snapshot_b.rights[0], delivery="available",
        delivery_offer_id="second-borrower-offer",
    ),))
    persist_attested_source_references(storage, snapshot_a)
    persist_attested_source_references(storage, snapshot_b)
    service.current_identity = NS(identity_id="A")
    monkeypatch.setattr(
        "cifereca.core.loan_download_retry_review.acquisition_lock_file",
        lambda: lock_path,
    )

    attempts = []
    def temporary_failure(key, right):
        attempts.append((key.scope.account_id, right.delivery_offer_id))
        raise RetryableContentDownload("synthetic transport interrupted")

    for index in range(FAILURE_LIMIT):
        rotated = replace(snapshot_a, rights=(replace(
            snapshot_a.rights[0], delivery_offer_id=f"fresh-A-{index}",
        ),))
        result, = run(
            service, borrower_a, rotated, root, lock_path, {source_a},
            temporary_failure,
        )
        assert result.status == "failed"
    assert read_loan_download_retry(storage, source_a).paused
    assert read_loan_download_retry(storage, source_b).failures == 0

    # The independent borrower on the SAME authenticated credential can
    # publish its own permitted resource, without borrowing A's retry budget.
    published, = run(
        service, borrower_b, snapshot_b, root, lock_path, {source_b},
        fake_delivery(service, root),
    )
    assert published.status == "downloaded"
    assert storage.placement_for_path(root / "A" / "book.pdf")["identity_id"] == "A"
    assert rows(storage) == (1, 1, 2, 1)
    assert read_loan_download_retry(storage, source_a).paused
    assert read_loan_download_retry(storage, source_b).failures == 0

    # Even though the bytes live in the same physical profile and SourceKey
    # differs only in borrower, B's provider receipt cannot satisfy A.
    blocked, = run(
        service, borrower_a, snapshot_a, root, lock_path, {source_a},
        lambda *_: pytest.fail("paused borrower A must not issue a fresh GET"),
    )
    assert (blocked.status, blocked.reason) == ("blocked", "loan_download_paused")
    assert len(attempts) == FAILURE_LIMIT and service.calls == 1
    assert rows(storage) == (1, 1, 2, 1)

    queued, = review_loan_download_retries(service, identity_id="A")
    assert queued.source == source_a and queued.can_reset
    reset_reviewed_loan_download_retry(service, identity_id="A", row=queued)
    assert read_loan_download_retry(storage, source_a).failures == 0
    assert read_loan_download_retry(storage, source_b).failures == 0
    assert review_loan_download_retries(service, identity_id="A") == ()
    assert rows(storage) == (1, 1, 2, 1)
