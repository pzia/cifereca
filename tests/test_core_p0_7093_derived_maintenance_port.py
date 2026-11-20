"""#7093: only scoped observations and one-shot dry-run through public DTOs."""
from pathlib import Path

import pytest

from cifereca.core.derived_maintenance_broker import DerivedMaintenanceReadBrokerImpl
from cifereca.plugin_api.derived_maintenance import DerivedMaintenanceReadBroker, DerivedReceiptPage
from tests.test_core_p0_4520_recovery_operator import _setup


def test_public_capacity_overview_and_simulation_preserve_receipts(tmp_path):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    try:
        broker = DerivedMaintenanceReadBrokerImpl(service)
        assert isinstance(broker, DerivedMaintenanceReadBroker)
        before = db.metadata_get(reservation.key)
        assert broker.overview(identity_id="id1") is None
        assert broker.capacity(identity_id="other", confirmed=True) is None
        overview = broker.overview(identity_id="id1", confirmed=True)
        assert overview.checked == overview.linked_pending == 1
        capacity = broker.capacity(identity_id="id1", confirmed=True)
        assert capacity.record_count == 1
        assert capacity.known_output_bytes == intent.output_size == capacity.logical_bytes
        assert capacity.compact_status == "observed"
        assert not hasattr(capacity, "recovery_authorized")
        assert broker.list_receipts(identity_id="id1") is None
        page = broker.list_receipts(identity_id="id1", confirmed=True)
        assert type(page) is DerivedReceiptPage and page.complete and not page.has_more
        choice, = page.choices
        assert choice.parent_relative == "Book.epub" and choice.target_relative == "variant.epub"
        assert not hasattr(choice, "reservation") and not hasattr(page, "scope")
        assert broker.verify(token=choice.token) is None
        handle = broker.verify(token=choice.token, confirmed=True)
        assert type(handle) is str and handle != choice.token
        assert broker.simulate(token=handle) == "review_required"
        assert broker.simulate(token=handle, confirmed=True) == "verified_no_write"
        assert broker.simulate(token=handle, confirmed=True) == "review_required"
        assert db.metadata_get(reservation.key) == before and len(db.placements()) == 1
        assert Path(intent.staged_path).samefile(intent.candidate_path)
    finally:
        db.close()


@pytest.mark.parametrize("change", ["epoch", "sql", "cancel", "revoke", "new_page", "unlink"])
def test_stale_review_token_cannot_simulate(tmp_path, change):
    service, intent, reservation = _setup(tmp_path)
    db = service.storage
    try:
        broker = DerivedMaintenanceReadBrokerImpl(service)
        page = broker.list_receipts(identity_id="id1", confirmed=True)
        token = broker.verify(token=page.choices[0].token, confirmed=True)
        assert token is not None
        before = db.metadata_get(reservation.key)
        if change == "epoch":
            service._account_context_epoch += 2
        elif change == "sql":
            db.metadata_set("synthetic-change", "new")
        elif change == "cancel":
            broker.cancel(token=page.token)
        elif change == "revoke":
            broker.revoke()
        elif change == "new_page":
            assert broker.list_receipts(identity_id="id1", confirmed=True).token != page.token
        else:
            Path(intent.candidate_path).unlink()
        assert broker.simulate(token=token, confirmed=True) == "review_required"
        assert db.metadata_get(reservation.key) == before and len(db.placements()) == 1
        assert Path(intent.staged_path).exists()
        if change == "revoke":
            assert broker.list_receipts(identity_id="id1", confirmed=True) is None
            assert broker.overview(identity_id="id1", confirmed=True) is None
    finally:
        db.close()
