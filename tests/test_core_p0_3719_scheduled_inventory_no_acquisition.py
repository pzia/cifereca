"""P0 #3719: scheduled discovery never executes the A→B acquisition route.

Real disposable Core SQLite; synthetic v2 provider, no network or ADEPT.
"""
from dataclasses import replace
import json

import pytest

from cifereca.core.source_sync_consent import policy_key

from cifereca.core.sync_coordination import synchronize_account_source
from tests.test_core_p0_3719_v2_execution import v2
from tests.test_core_p0_3171_canonical_sync_stage import rig, refs


@pytest.mark.parametrize("consent_mode", ["legacy", "source_wide"])
def test_inventory_only_observes_a_without_selected_offer_or_get_even_when_consent_on(v2, consent_mode):
    service, db, root, calls = v2
    # Synthetic Core service models only the A observation, with no legacy
    # personal-library reads. Its dedicated scheduled implementation is the
    # same metadata-only fixture used by manual Sync.
    service._synchronize_account_source_inventory_impl = (
        service._synchronize_account_source_impl
    )
    service.resource_snapshot = replace(
        service.resource_snapshot, status="success", coverage="partial",
    )
    if consent_mode == "source_wide":
        # The canonical source-wide ON policy is persisted independently of
        # the scheduled inventory opt-in; it must not trigger A→B here.
        scope = service.resource_snapshot.scope
        db.metadata_set(policy_key(scope), json.dumps({
            "schema": "cifereca.source-sync-consent/1",
            "scope": [scope.identity_id, scope.provider_id,
                      scope.credential_id, scope.account_id],
            "enabled": True, "revision": "a" * 32,
        }))
        service._canonical_persisted_consent = None

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


def test_inventory_only_skips_legacy_bnf_auto_materialization(monkeypatch):
    """An installed legacy loan/ACSM option must never escape discover-rights."""
    from types import SimpleNamespace as NS

    from cifereca.core import sync_coordination
    from cifereca.core import bnf_auto_materialization
    from cifereca.models import Account

    account = Account(
        identity_id="A", provider_id="bnf-pret", credential_id="card",
        unique_id="borrower",
    )
    session = NS(
        connected=True, capabilities=NS(loans=True, personal_library=False),
    )
    service = NS(
        accounts=[account], _source_context_epoch=1,
        _sessions={("bnf-pret", "card"): session},
        _session_key=lambda provider, credential: (provider, credential),
        _session_for_account=lambda _account: session,
        sync_source_enabled=lambda _account: True,
        identity_by_id=lambda _identity: NS(identity_id="A"),
    )
    monkeypatch.setattr(
        sync_coordination, "_sync_loan_inventory_observation",
        lambda *_args: (None, []),
    )
    monkeypatch.setattr(
        sync_coordination, "source_consent_configured",
        lambda *_args: False,
    )
    legacy_checks = []
    monkeypatch.setattr(
        bnf_auto_materialization, "enabled",
        lambda *_args: (legacy_checks.append("checked") or False),
    )

    observed = sync_coordination._synchronize_account_source_impl(
        service, account, inventory_only=True,
    )
    assert observed["loans"] == 0
    assert legacy_checks == []

    sync_coordination._synchronize_account_source_impl(service, account)
    assert legacy_checks == ["checked"]  # Manual legacy route remains available.
