"""Scheduled private-store inventory must not treat unknown phase statuses as idle."""
from datetime import datetime, timezone

import pytest

from cifereca.core.source_refresh_policy import (
    SourceRefreshSchedule, source_refresh_due,
)


@pytest.mark.parametrize("status", [
    "unknown", "blocked", "cancelled", "interrupted",
    "attention_required", "error", "", "RUNNING", None, True,
])
def test_unknown_inventory_phase_never_authorizes_automatic_refresh(status):
    assert source_refresh_due(
        SourceRefreshSchedule(mode="periodic", interval_minutes=60),
        trigger="periodic", now=datetime(2026, 10, 17, tzinfo=timezone.utc),
        phase_status=status, enabled=True, connected=True, capable=True,
    ) is False


@pytest.mark.parametrize("status", ["idle", "success", "partial", "failed"])
def test_known_nonrunning_inventory_phase_is_eligible_without_previous_attempt(status):
    assert source_refresh_due(
        SourceRefreshSchedule(mode="periodic", interval_minutes=60),
        trigger="periodic", now=datetime(2026, 10, 17, tzinfo=timezone.utc),
        phase_status=status, enabled=True, connected=True, capable=True,
    ) is True


def test_running_inventory_phase_cannot_reenter_periodic_refresh():
    assert source_refresh_due(
        SourceRefreshSchedule(mode="periodic", interval_minutes=60),
        trigger="periodic", now=datetime(2026, 10, 17, tzinfo=timezone.utc),
        phase_status="running", enabled=True, connected=True, capable=True,
    ) is False
