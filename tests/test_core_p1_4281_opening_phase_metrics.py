"""P1 #4281: opening-sync phase time is measured independently of HTTP.

Fixtures are synthetic; recorded elapsed values are wall-clock costs of Core
reader invocations, not claims about Paris/BnF server latency or GET counts.
"""
from __future__ import annotations

from cifereca.core import canonical_sync_stage, sync_coordination
from cifereca.models import Account
from tests.synthetic_lending_sessions import enable_synthetic_paris_opening
from tests.test_sync_state_contract import _service


def _accounts(service):
    accounts = [
        Account(
            display_name=f"Reader {i}", unique_id=f"reader-{i}",
            provider_id="paris", credential_id="card", identity_id="default",
        ) for i in (1, 2)
    ]
    service.accounts = accounts
    enable_synthetic_paris_opening(service)
    return accounts


def test_opening_records_bounded_phase_counts_for_each_scoped_source(tmp_path, monkeypatch):
    service = _service(tmp_path)
    accounts = _accounts(service)
    invocations = []

    def loan_read(_service, account, _session):
        invocations.append(("loans", account.unique_id))
        return None, []

    def resource_read(_service, account, **_kwargs):
        invocations.append(("resources", account.unique_id))
        return None

    monkeypatch.setattr(sync_coordination, "_sync_loan_inventory_observation", loan_read)
    monkeypatch.setattr(canonical_sync_stage, "observe_canonical_rights_in_sync", resource_read)
    try:
        result = service.opening_sync()
        assert invocations == [
            ("loans", accounts[0].unique_id), ("resources", accounts[0].unique_id),
            ("loans", accounts[1].unique_id), ("resources", accounts[1].unique_id),
        ]
        row = next(r for r in result["identities"] if r["identity_id"] == "default")
        timings = row["source_observation_timing"]
        assert set(timings) == {"loan_inventory", "canonical_resources"}
        assert all(
            entry["attempts"] == 2
            and type(entry["elapsed_ms"]) is int
            and 0 <= entry["elapsed_ms"] <= 86_400_000
            for entry in timings.values()
        )
        assert "reader-1" not in str(timings) and "reader-2" not in str(timings)
        assert service.storage.latest_sync_run("default")["detail"]["source_observation_timing"] == timings
        other = next(r for r in result["identities"] if r["identity_id"] == "alex")
        assert "source_observation_timing" not in other
    finally:
        service.close()


def test_opening_failed_inventory_attempt_is_counted_without_rights_fallback(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    service.accounts = _accounts(service)[:1]
    calls = []

    def loan_failure(_service, account, _session):
        calls.append("loans")
        raise RuntimeError("synthetic inventory read failure")

    monkeypatch.setattr(sync_coordination, "_sync_loan_inventory_observation", loan_failure)
    monkeypatch.setattr(
        canonical_sync_stage, "observe_canonical_rights_in_sync",
        lambda *_args, **_kwargs: calls.append("unexpected-rights"),
    )
    try:
        result = service.opening_sync()
        assert calls == ["loans"]
        row = next(r for r in result["identities"] if r["identity_id"] == "default")
        assert row["source_observation_timing"]["loan_inventory"]["attempts"] == 1
        assert "canonical_resources" not in row["source_observation_timing"]
    finally:
        service.close()


def test_opening_measurement_rejects_invalid_elapsed_and_unknown_phase(monkeypatch):
    detail = {}
    # Synthetic elapsed duration: no dependency on slow external requests.
    monkeypatch.setattr(sync_coordination.time, "monotonic", lambda: 10.5)
    sync_coordination._record_opening_observation_timing(detail, "loan_inventory", 10.375)
    sync_coordination._record_opening_observation_timing(detail, "canonical_resources", 10.25)
    sync_coordination._record_opening_observation_timing(detail, "other", 10.0)
    sync_coordination._record_opening_observation_timing(detail, "loan_inventory", 11.0)
    assert detail == {
        "source_observation_timing": {
            "loan_inventory": {"attempts": 1, "elapsed_ms": 125},
            "canonical_resources": {"attempts": 1, "elapsed_ms": 250},
        },
    }
