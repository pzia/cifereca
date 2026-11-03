"""P0 #3719: scheduled discovery never executes the A→B acquisition route.

Real disposable Core SQLite; synthetic v2 provider, no network or ADEPT.
"""
from dataclasses import replace

from cifereca.core.sync_coordination import synchronize_account_source
from tests.test_core_p0_3719_v2_execution import v2
from tests.test_core_p0_3171_canonical_sync_stage import refs


def test_inventory_only_observes_a_without_selected_offer_or_get_even_when_consent_on(v2):
    service, db, root, calls = v2
    # The fake service predates the internal inventory-only flag. Keep its
    # existing A fixture but expose the new restrictive dispatch explicitly.
    original = service._synchronize_account_source_impl
    service._synchronize_account_source_impl = (
        lambda account, *, deep=False, control=None, inventory_only=False:
        original(account, deep=deep, control=control)
    )
    service.resource_snapshot = replace(
        service.resource_snapshot, status="success", coverage="partial",
    )

    discovery = synchronize_account_source(
        service, service.account, inventory_only=True,
    )
    assert discovery["status"] == "ok"
    assert discovery["canonical_rights"]["status"] == "success"
    assert discovery["canonical_rights"]["observed"] == 1
    assert discovery["canonical_rights"]["pending"] == 1
    assert discovery["canonical_rights"]["acquisition_authorized"] is False
    assert "canonical_execution" not in discovery
    assert "files_materialized" not in discovery
    assert calls == []  # No selected offer, signed content GET or ADEPT.
    assert len(refs(db)[0]) == 1 and refs(db)[1] == 0
    assert db.placements() == []

    # A normal explicit source Sync is unchanged: the same *already* granted
    # right may still execute once under the canonical Core A→B policy.
    manual = synchronize_account_source(service, service.account)
    assert manual["canonical_execution"]["downloaded"] == 1
    assert calls == ["offer", "delivery"]
    assert refs(db)[1] == 1 and len(db.placements()) == 1
