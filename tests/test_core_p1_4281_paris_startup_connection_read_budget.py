"""#4281: end-to-end GET budget from real Core connection to Paris readers.

The provider and Core functions are real; only login and upstream ListLoans
are synthetic. These assertions describe request counts, not remote latency.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.core import provider_sessions
from cifereca.core.sync_coordination import _sync_loan_inventory_observation
from tests.test_core_p0_1143_provider_connect_privacy import _rig
from tests.test_core_p1_4281_paris_opening_observer_cost import _session


def _connected_paris(digital):
    paris, account, client = _session(digital)
    # We intentionally exercise the actual Paris loan/snapshot methods, but
    # do not authenticate a real Paris card over HTTP.
    paris.connect = Mock(return_value=[account])
    service, _discarded_session, plugin = _rig(succeeds=True)
    service.current_identity = NS(identity_id="identity-A")
    service.config.provider_accounts = lambda **_kw: [{
        "id": "card-A", "provider": "paris", "identity_id": "identity-A",
        "credentials": {"password": "synthetic"}, "enabled": True,
    }]
    plugin.descriptor.id = "paris"
    plugin.descriptor.display_name = "Paris synthetic"
    plugin.create_session = Mock(return_value=paris)
    service.accounts_for_identity = lambda identity_id: [
        item for item in service.accounts if item.identity_id == identity_id
    ]
    service.sync_source_enabled = Mock(return_value=True)
    service.refresh_identity_loans = Mock(
        side_effect=lambda **_kwargs: paris.loans(account)
    )
    return service, paris, account, client


@pytest.mark.parametrize("digital", [0, 1, 4, 20])
@pytest.mark.parametrize("caller_category,expected_gets", [
    ("startup/connect_configured", 4),
    ("explicit_refresh", 6),
])
def test_connection_plus_opening_sync_keeps_exact_fresh_paris_read_budget(
    digital, caller_category, expected_gets,
):
    service, paris, account, client = _connected_paris(digital)
    try:
        result = provider_sessions.connect_configured(
            service, logger=logging.getLogger("cifereca"),
            caller_category=caller_category,
        )
        assert result["errors"] == {}
        assert result["connected"] == ["paris:card-A"]
        assert result["accounts"] == [account]
        assert paris.connect.call_count == 1
        if caller_category == "startup/connect_configured":
            service.refresh_identity_loans.assert_not_called()
            assert result["loans"] == []
            assert client.calls == []
        else:
            service.refresh_identity_loans.assert_called_once_with(force=True)
            assert len(result["loans"]) == digital
            assert client.calls == [
                ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
            ]

        # These are the SAME two independent read contracts called by Core's
        # opening Sync. Do not substitute the display list for fresh rights.
        snapshot, loans = _sync_loan_inventory_observation(
            service, account, paris,
        )
        resources = paris.downloadable_resource_snapshot(account)

        assert snapshot.status == "success"
        assert len(loans) == digital
        assert resources.status == "success"
        assert len(resources.resources) == digital
        assert len(client.calls) == expected_gets
        assert client.calls == [
            ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
        ] * (expected_gets // 2)
        assert all(
            row.source.scope.identity_id == "identity-A"
            and row.source.scope.account_id == "reader-A"
            for row in resources.resources
        )
    finally:
        paris.close()


def test_startup_source_outside_sync_keeps_initial_pair_before_canonical_reads():
    service, paris, account, client = _connected_paris(1)
    service.sync_source_enabled.return_value = False
    try:
        result = provider_sessions.connect_configured(
            service, logger=logging.getLogger("cifereca"),
            caller_category="startup/connect_configured",
        )
        assert result["connected"] == ["paris:card-A"]
        service.refresh_identity_loans.assert_called_once_with(force=True)
        assert client.calls == [
            ("DILICOM", "reader-A"), ("SYRACUSE", "reader-A"),
        ]
    finally:
        paris.close()
