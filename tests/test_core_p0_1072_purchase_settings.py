"""Independent preference CAS, no Sync, no mutation of A/B/C."""
from threading import RLock
from types import SimpleNamespace
import pytest

from cifereca.core.notice_c_purchase_settings import (
    PurchasePolicy, _REVISION, read_purchase_policy, change_purchase_policy,
)
from cifereca.core.notice_c_purchase_intent import _POLICY
from cifereca.storage import Storage


def test_policy_default_cas_aba_and_restart(tmp_path):
    path = tmp_path / "policy.sqlite"
    db = Storage(path)
    service = SimpleNamespace(storage=db, _source_context_lock=RLock())
    try:
        before = read_purchase_policy(db)
        assert before == PurchasePolicy(True, 0)
        assert db.metadata_get(_POLICY) is None
        off = change_purchase_policy(service, expected_storage=db, expected=before, enabled=False)
        assert off == PurchasePolicy(False, 1)
        with pytest.raises(ValueError, match="policy_changed"):
            change_purchase_policy(service, expected_storage=db, expected=before, enabled=True)
        on = change_purchase_policy(service, expected_storage=db, expected=off, enabled=True)
        assert on == PurchasePolicy(True, 2)
        assert change_purchase_policy(service, expected_storage=db, expected=on, enabled=True) == on
        with pytest.raises(ValueError, match="policy_changed"):
            change_purchase_policy(service, expected_storage=db, expected=before, enabled=False)
        assert db.entitlements() == [] and db.acquisitions() == [] and db.placements() == []
    finally:
        db.close()
    db = Storage(path)
    try:
        assert read_purchase_policy(db) == on
        db.metadata_set(_REVISION, "bad")
        with pytest.raises(ValueError, match="policy_corrupt"):
            read_purchase_policy(db)
    finally:
        db.close()
