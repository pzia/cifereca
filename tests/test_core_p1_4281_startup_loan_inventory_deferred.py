"""P1 #4281 — startup display inventory is deferred to opening Sync.

The first GUI connection schedules an opening Sync with fresh typed reads.
Do not trigger an extra loan GET before it, and do not reuse stale rights.
Explicit reconnect, CLI and targeted connection keep the existing fresh read.
All connections are synthetic; no actual Paris/BnF network is invoked.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.core import provider_sessions
from tests.test_core_p0_1143_provider_connect_privacy import _rig, SECRET


@pytest.mark.parametrize("caller_category,read_expected", [
    ("startup/connect_configured", False),
    ("explicit_refresh", True),
    ("plugin_activation", True),
    ("profile_change", True),
    ("other", True),
])
def test_startup_is_only_global_connection_that_defers_forced_loan_read(
    caller_category, read_expected,
):
    service, session, _plugin = _rig(succeeds=True)
    refreshed = [NS(id="synthetic-loan")]
    service.refresh_identity_loans = Mock(return_value=refreshed)
    # No previous snapshot may survive a reconnect, even if the worker
    # intentionally defers its inventory for the first opening Sync.
    service.last_loans = [NS(id="old-account-loan")]
    result = provider_sessions.connect_configured(
        service, logger=logging.getLogger("cifereca"),
        caller_category=caller_category,
    )
    assert result["connected"] == ["provider.test:" + SECRET]
    assert session.connect.call_count == 1
    assert len(result["accounts"]) == 1
    if read_expected:
        service.refresh_identity_loans.assert_called_once_with(force=True)
        assert result["loans"] is refreshed
    else:
        service.refresh_identity_loans.assert_not_called()
        assert result["loans"] == []
        assert service.last_loans == []


def test_subsequent_explicit_reconnect_still_performs_fresh_inventory():
    service, session, _plugin = _rig(succeeds=True)
    observed = [NS(id="synthetic-fresh")]
    service.refresh_identity_loans = Mock(return_value=observed)
    first = provider_sessions.connect_configured(
        service, logger=logging.getLogger("cifereca"),
        caller_category="startup/connect_configured",
    )
    assert first["loans"] == []
    service.refresh_identity_loans.assert_not_called()
    second = provider_sessions.connect_configured(
        service, logger=logging.getLogger("cifereca"),
        caller_category="explicit_refresh",
    )
    assert session.connect.call_count == 2
    service.refresh_identity_loans.assert_called_once_with(force=True)
    assert second["loans"] is observed
    assert service.last_loans == []  # facade fake does not publish loans


def test_targeted_connection_is_not_treated_as_gui_startup():
    service, session, _plugin = _rig(succeeds=True)
    service.refresh_identity_loans = Mock(return_value=[])
    result = provider_sessions.connect_configured(
        service, logger=logging.getLogger("cifereca"),
        caller_category="startup/connect_configured",
        source_scope=("identity-private", "provider.test", SECRET),
    )
    assert len(result["accounts"]) == 1
    service.refresh_identity_loans.assert_called_once_with(force=True)


def test_failed_connection_does_not_create_deferred_private_read():
    service, session, _plugin = _rig(succeeds=False)
    service.refresh_identity_loans = Mock(return_value=[])
    result = provider_sessions.connect_configured(
        service, logger=logging.getLogger("cifereca"),
        caller_category="startup/connect_configured",
    )
    assert not result["connected"]
    assert result["loans"] == []
    assert service.last_loans == []
    service.refresh_identity_loans.assert_not_called()
