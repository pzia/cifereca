"""P0 #3171: source-sync terminal reports Canonical A→B outcome, not inventory alone.

Real SQLite and Core executor on a synthetic v2 provider; no external HTTP.
"""
from __future__ import annotations

import pytest

from cifereca.core.diagnostic_source_sync import (
    CANONICAL_EXECUTION_STATUSES, log_message, public_fields,
    public_log_fields,
)
from cifereca.core.sync_coordination import synchronize_account_source
from cifereca.diagnostics import Diagnostics
from tests.test_core_p0_3171_canonical_sync_stage import rig
from tests.test_core_p0_3719_v2_execution import v2


def _terminal(service):
    records = [
        row["detail"] for row in service.diagnostics.snapshot()
        if row.get("verified_source_sync")
        and row["detail"]["phase"] == "completed"
    ]
    assert records
    return records[-1]


def _payload(status="partial"):
    return dict(
        schema_version=1, provider_id="provider.test",
        operation_kind="source_sync", phase="completed", status=status,
        code="source_sync_partial" if status == "partial" else "source_sync_complete",
    )


def _counts(**changes):
    counts = {state: 0 for state in CANONICAL_EXECUTION_STATUSES}
    counts.update(changes)
    return counts


def test_real_core_sync_reports_download_then_attested_local_presence(v2):
    service, db, root, calls = v2
    # The generic v2 fixture has an inert diagnostics sink; attach the real
    # production collector for this observable terminal contract.
    service.diagnostics = Diagnostics()
    first = synchronize_account_source(service, service.account)
    assert first["canonical_execution"]["downloaded"] == 1
    event = _terminal(service)
    assert event["canonical_downloaded"] == 1
    assert event["canonical_present_attested"] == 0
    assert event["canonical_pending"] == 0
    assert public_log_fields(log_message(event)) == event
    second = synchronize_account_source(service, service.account)
    assert second["canonical_execution"]["present_attested"] == 1
    event = _terminal(service)
    assert event["canonical_downloaded"] == 0
    assert event["canonical_present_attested"] == 1
    assert calls == ["offer", "delivery"]
    assert len(db.placements()) == 1


def test_closed_terminal_projection_is_identical_in_memory_and_log():
    diag = Diagnostics()
    counts = {"library": 6, "canonical_execution": _counts(
        downloaded=3, pending=2, blocked=1,
    )}
    diag.add_core_source_sync(
        "provider.test", phase="completed", status="partial",
        code="source_sync_partial", counts=counts,
    )
    row = diag.snapshot()[-1]["detail"]
    assert row["library"] == 6
    assert row["canonical_downloaded"] == 3
    assert row["canonical_pending"] == 2
    assert row["canonical_blocked"] == 1
    assert public_log_fields(log_message(row)) == row
    assert "credential_id" not in row
    assert "item_id" not in row


@pytest.mark.parametrize("malformed", [
    {"downloaded": True}, {"pending": -1}, {"failed": 1_000_001},
    {"recovered": "1"}, {"source_id": "PRIVATE"},
])
def test_malformed_executor_snapshot_never_forges_canonical_counters(malformed):
    diag = Diagnostics()
    summary = _counts()
    summary.update(malformed)
    diag.add_core_source_sync(
        "provider.test", phase="completed", status="partial",
        code="source_sync_partial",
        counts={"canonical_execution": summary},
    )
    fields = diag.snapshot()[-1]["detail"]
    assert not any(k.startswith("canonical_") for k in fields)


@pytest.mark.parametrize("value", [True, -1, 1_000_001, "PRIVATE", {}, None])
def test_public_schema_rejects_untrusted_canonical_counter(value):
    row = {**_payload(), **{
        "canonical_" + state: 0 for state in CANONICAL_EXECUTION_STATUSES
    }}
    row["canonical_downloaded"] = value
    assert public_fields(row) == {}


def test_canonical_counters_are_terminal_only_and_complete():
    fields = {**_payload(), **{
        "canonical_" + state: 0 for state in CANONICAL_EXECUTION_STATUSES
    }}
    assert public_fields(fields) == fields
    assert public_fields({**fields, "canonical_failed": 1}) == {
        **fields, "canonical_failed": 1,
    }
    assert public_fields({k: v for k, v in fields.items()
                          if k != "canonical_failed"}) == {}
    assert public_fields({
        **fields, "phase": "start", "status": "running",
        "code": "source_sync_started",
    }) == {}
    assert public_fields({
        **fields, "phase": "completed", "status": "failed",
        "code": "source_sync_failed",
    }) == {}


def test_no_canonical_executor_does_not_invent_a_zeroed_report():
    diag = Diagnostics()
    diag.add_core_source_sync(
        "provider.test", phase="completed", status="partial",
        code="source_sync_partial", counts={"library": 6},
    )
    fields = diag.snapshot()[-1]["detail"]
    assert fields["library"] == 6
    assert not any(key.startswith("canonical_") for key in fields)
