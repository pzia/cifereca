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


def test_2498_valid_shared_budgets_are_public_but_no_path_or_identifier(monkeypatch):
    service, broker, source, calls = _broker()
    report = _report()
    report.update(
        files_hashed_limit=2048,
        directory_entries_limit=4096,
        total_hash_bytes_limit=64 * 1024 ** 3,
        total_hash_bytes_read=5 * 1024 ** 2,
        extra_private_budget_field="https://private.invalid/?token=PRIVATE",
    )
    _stub(monkeypatch, lambda: report)
    result = broker.audit_local_reconciliation(source.source_id)
    assert result.ok
    assert {key: result.facts[key] for key in (
        "files_hashed_limit", "directory_entries_limit",
        "total_hash_bytes_limit", "total_hash_bytes_read",
    )} == {key: report[key] for key in (
        "files_hashed_limit", "directory_entries_limit",
        "total_hash_bytes_limit", "total_hash_bytes_read",
    )}
    assert "PRIVATE" not in repr(result) and "private.invalid" not in repr(result)
    assert calls == [] and service.calls == []


@pytest.mark.parametrize("mutation", [
    lambda r: r.pop("total_hash_bytes_read"),
    lambda r: r.update(files_hashed_limit=True),
    lambda r: r.update(files_hashed_limit=-1),
    lambda r: r.update(directory_entries_limit="PRIVATE_PATH"),
    lambda r: r.update(total_hash_bytes_limit=10 ** 15),
    lambda r: r.update(total_hash_bytes_read=10 ** 12),
    lambda r: r.update(total_hash_bytes_read="https://private.invalid/?token=PRIVATE"),
])
def test_2498_partial_or_hostile_shared_budgets_fail_closed(monkeypatch, mutation):
    service, broker, source, _ = _broker()
    report = _report()
    report.update(
        files_hashed_limit=2048,
        directory_entries_limit=4096,
        total_hash_bytes_limit=64 * 1024 ** 3,
        total_hash_bytes_read=5 * 1024 ** 2,
    )
    mutation(report)
    _stub(monkeypatch, lambda: report)
    result = broker.audit_local_reconciliation(source.source_id)
    assert not result.ok and result.facts["status"] == "audit_unavailable"
    assert "PRIVATE" not in repr(result) and "private.invalid" not in repr(result)


def test_2498_closed_truncation_causes_and_actual_attempt_counts_are_public(
    monkeypatch,
):
    service, broker, source, _ = _broker()
    report = _report()
    report.update(
        truncated=True,
        files_hashed_limit=2048, directory_entries_limit=4096,
        total_hash_bytes_limit=64 * 1024 ** 3,
        total_hash_bytes_read=1253 * 1024 ** 2,
        truncation_reasons=["directory_scan_unavailable", "product_path_budget"],
        hash_attempts_used=517, directory_entries_inspected=520,
        private_diagnostic="https://private.invalid?token=PRIVATE",
    )
    _stub(monkeypatch, lambda: report)
    result = broker.audit_local_reconciliation(source.source_id)
    assert result.ok
    assert result.facts["truncation_reasons"] == [
        "directory_scan_unavailable", "product_path_budget",
    ]
    assert result.facts["hash_attempts_used"] == 517
    assert result.facts["directory_entries_inspected"] == 520
    assert "PRIVATE" not in repr(result)
    assert "private.invalid" not in repr(result)
    assert "private_diagnostic" not in repr(result)


@pytest.mark.parametrize("mutation", [
    lambda r: r.pop("hash_attempts_used"),
    lambda r: r.pop("truncation_reasons"),
    lambda r: r.update(truncation_reasons="directory_scan_unavailable"),
    lambda r: r.update(truncation_reasons=["PRIVATE_KEY"]),
    lambda r: r.update(truncation_reasons=[True]),
    lambda r: r.update(truncation_reasons=["hash_byte_budget", "hash_byte_budget"]),
    lambda r: r.update(truncation_reasons=["product_path_budget", "hash_byte_budget"]),
    lambda r: r.update(truncation_reasons=[]),
    lambda r: r.update(hash_attempts_used=True),
    lambda r: r.update(hash_attempts_used=2049),
    lambda r: r.update(hash_attempts_used="PRIVATE_ACCOUNT"),
    lambda r: r.update(directory_entries_inspected=-1),
    lambda r: r.update(directory_entries_inspected=4097),
    lambda r: r.update(directory_entries_inspected={"secret": "PRIVATE"}),
    lambda r: r.update(truncated=False),
    lambda r: r.pop("files_hashed_limit"),
])
def test_2498_malformed_truncation_evidence_is_rejected_at_broker(
    monkeypatch, mutation,
):
    service, broker, source, _ = _broker()
    report = _report()
    report.update(
        truncated=True,
        files_hashed_limit=2048, directory_entries_limit=4096,
        total_hash_bytes_limit=64 * 1024 ** 3,
        total_hash_bytes_read=1253 * 1024 ** 2,
        truncation_reasons=["directory_scan_unavailable"],
        hash_attempts_used=517, directory_entries_inspected=520,
    )
    mutation(report)
    _stub(monkeypatch, lambda: report)
    result = broker.audit_local_reconciliation(source.source_id)
    assert result.ok is False
    assert result.facts["status"] == "audit_unavailable"
    assert result.facts["acquisition_authorized"] is False
    assert "PRIVATE" not in repr(result)
    assert "directory_scan_unavailable" not in repr(result)


@pytest.mark.parametrize("origin", [
    "backup_records_budget", "catalogue_rows_budget",
    "active_entitlements_budget", "record_budget",
])
def test_2498_broker_allows_only_canonical_persisted_row_origin_codes(
    monkeypatch, origin,
):
    service, broker, source, _ = _broker()
    report = _report()
    report.update(
        truncated=True,
        files_hashed_limit=2048, directory_entries_limit=4096,
        total_hash_bytes_limit=64 * 1024 ** 3, total_hash_bytes_read=0,
        truncation_reasons=[origin],
        hash_attempts_used=0, directory_entries_inspected=0,
        secret="PRIVATE-ACCOUNT",
    )
    _stub(monkeypatch, lambda: report)
    result = broker.audit_local_reconciliation(source.source_id)
    assert result.ok
    assert result.facts["truncation_reasons"] == [origin]
    assert "PRIVATE" not in repr(result)
