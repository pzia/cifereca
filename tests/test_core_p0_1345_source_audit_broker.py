"""P0 #1345/#1347: no provider calls or account data escape from scoped audit."""
from __future__ import annotations

import json
from copy import deepcopy

import pytest

from cifereca.plugin_api import SynchronizationBroker
from tests.test_core_1346_source_preflight import _broker


COUNTS = {
    "backup_rows": 4, "linked_files_verified": 3,
    "unverified_placements": 1, "missing_or_unlinked": 0,
    "stored_hash_mismatch": 1, "unproven_catalogue_links": 0,
    "same_product_different_bytes": 0, "binary_match_groups": 1,
    "active_entitlements_unmaterialized": 2,
    "unindexed_regular_file_candidates": 1,
    "identity_flat_file_candidates": 0,
}


def _report():
    return {
        "schema": "kobo.local.reconciliation.audit/1",
        "scope": "single_account", "offline": True,
        "read_only": True, "dry_run": True,
        "downloads_authorized": False, "mutations_authorized": False,
        "truncated": False, "counts": dict(COUNTS),
        "accounts": [{"account": "account-1", "counts": dict(COUNTS)}],
        "cases": [{
            "case": "case-001", "category": "confirmed_binary_match",
            "artifacts": 2, "decision": "manual_review_only",
            "secret": "/home/PRIVATE_SECRET.epub",
        }],
        "private_path": "/home/PRIVATE_SECRET.epub",
        "private_token": "https://host.invalid/?token=PRIVATE_TOKEN",
    }


def _stub(monkeypatch, callback):
    import cifereca.core.kobo_local_audit as audit_module
    calls = []
    def audited(service, *, credential_id):
        calls.append(credential_id)
        return callback()
    monkeypatch.setattr(audit_module, "kobo_local_reconciliation_audit", audited)
    return calls


def test_protocol_exposes_scoped_audit_without_any_automatic_execution(monkeypatch):
    service, broker, source, provider_calls = _broker()
    calls = _stub(monkeypatch, _report)
    assert isinstance(broker, SynchronizationBroker)
    assert calls == provider_calls == []
    initial = dict(service.storage.values)
    result = broker.audit_local_reconciliation(source.source_id)
    assert calls == ["vault-secret"]
    assert provider_calls == []
    assert service.calls == []
    assert service.storage.values == initial
    assert result.ok and result.changed == 0
    assert result.facts == {
        "phase": "reconcile", "status": "audit_only",
        "read_only": True, "acquisition_authorized": False,
        "offline": True, "truncated": False, "counts": COUNTS,
    }
    for private in ("PRIVATE", "vault-secret", "reader-2", "account-1", "https://", "/home/"):
        assert private not in repr(result)


def test_disabled_and_offline_kobo_may_audit_without_acquiring(monkeypatch):
    service, broker, source, provider_calls = _broker()
    broker.set_enabled(source.source_id, False)
    session = next(iter(service._sessions.values()))
    session.connected = False
    calls = _stub(monkeypatch, _report)
    initial = dict(service.storage.values)
    result = broker.audit_local_reconciliation(source.source_id)
    assert result.ok
    assert calls == ["vault-secret"]
    assert provider_calls == []
    assert service.calls == []
    assert service.storage.values == initial


def test_unknown_or_other_provider_and_wrong_owner_fail_before_audit(monkeypatch):
    calls = _stub(monkeypatch, _report)
    service, broker, target, _ = _broker()
    other = broker.sources("alice")[0]
    for source_id, expected in (
        ("PRIVATE_UNLISTED", "unknown_source"),
        (other.source_id, "not_supported"),
    ):
        result = broker.audit_local_reconciliation(source_id)
        assert not result.ok and result.changed == 0
        assert result.facts["status"] == expected
    service.storage.identity_for_connection = lambda *_a: "PRIVATE_OTHER_ID"
    result = broker.audit_local_reconciliation(target.source_id)
    assert not result.ok and result.facts["status"] == "invalid_source"
    assert calls == []
    assert "PRIVATE" not in repr(result)


@pytest.mark.parametrize("mutation", [
    lambda row: row.update(schema="PRIVATE_SCHEMA"),
    lambda row: row.update(scope="all_configured"),
    lambda row: row.update(offline=False),
    lambda row: row.update(read_only=1),
    lambda row: row.update(dry_run=False),
    lambda row: row.update(downloads_authorized=True),
    lambda row: row.update(mutations_authorized=0),
    lambda row: row.update(truncated="false"),
    lambda row: row.update(accounts=[]),
    lambda row: row.update(accounts=[{"account": "PRIVATE_USER"}]),
    lambda row: row.update(counts={**row["counts"], "backup_rows": True}),
    lambda row: row.update(counts={**row["counts"], "backup_rows": -1}),
    lambda row: row.update(counts={**row["counts"], "backup_rows": 1_000_001}),
    lambda row: row.update(counts={**row["counts"], "PRIVATE_ID": 99}),
    lambda row: row.update(counts={"backup_rows": 1}),
])
def test_invalid_audit_payload_is_discarded_without_reflecting_secrets(monkeypatch, mutation):
    row = _report()
    mutation(row)
    service, broker, source, _ = _broker()
    _stub(monkeypatch, lambda: deepcopy(row))
    result = broker.audit_local_reconciliation(source.source_id)
    assert result.ok is False and result.changed == 0
    assert result.facts["status"] == "audit_unavailable"
    assert result.facts["acquisition_authorized"] is False
    for private in ("PRIVATE", "vault-secret", "/home/", "host.invalid"):
        assert private not in repr(result)


@pytest.mark.parametrize("failure,expected", [
    (ValueError("ambiguous_kobo_audit_scope"), "ambiguous_source"),
    (ValueError("PRIVATE_BAD_ACCOUNT"), "audit_unavailable"),
    (OSError("PRIVATE_PATH"), "audit_unavailable"),
    (RuntimeError("https://private.invalid/token"), "audit_unavailable"),
])
def test_audit_failures_are_fixed_codes_not_exception_details(monkeypatch, failure, expected):
    service, broker, source, _ = _broker()
    def fail():
        raise failure
    _stub(monkeypatch, fail)
    result = broker.audit_local_reconciliation(source.source_id)
    assert not result.ok
    assert result.facts["status"] == expected
    assert "PRIVATE" not in repr(result)
    assert "private.invalid" not in repr(result)


def test_audit_never_mutates_input_report(monkeypatch):
    service, broker, source, _ = _broker()
    report = _report()
    before = json.dumps(report, sort_keys=True)
    _stub(monkeypatch, lambda: report)
    assert broker.audit_local_reconciliation(source.source_id).ok
    assert json.dumps(report, sort_keys=True) == before
