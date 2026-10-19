"""#1120: TRACE preparation must not reveal loan, account or artifact paths."""
from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

import cifereca.services as facade
from cifereca.core.loan_safe_trace import loan_trace_ref
from tests.test_core_0977_adept_retry_guard import _service, _loan, _seed


def _local_records(caplog):
    return [
        record.getMessage() for record in caplog.records
        if str(record.pathname).endswith("loan_local_preparation.py")
    ]


def _not_private(events, loan):
    assert events
    combined = "\n".join(events)
    for value in (
        loan.id, loan.record_id, loan.title,
        loan.source_credential, loan.source_account_id, loan.identity_id,
        "SIGNED_SECRET", "/private/local/loan",
    ):
        assert value not in combined
    assert all("loan_ref=" in event for event in events)
    assert all("http" not in event.lower() for event in events)


def test_process_keyed_loan_refs_are_stable_scoped_and_not_original_values():
    source = _loan()
    first = loan_trace_ref(source, source.identity_id)
    assert len(first) == 20
    assert set(first).issubset(set("0123456789abcdef"))
    assert first == loan_trace_ref(source, source.identity_id)
    assert first != loan_trace_ref(replace(source, id="another-loan"), source.identity_id)
    assert first != loan_trace_ref(source, "different-identity")
    assert first != loan_trace_ref(
        replace(source, source_credential="different-account"),
        source.identity_id,
    )
    assert source.id not in first
    assert source.source_credential not in first


def test_automatic_provider_failure_keeps_decision_trace_but_redacts_source(
    tmp_path, monkeypatch, caplog,
):
    svc = _service(tmp_path)
    loan = _loan()
    caplog.set_level(logging.INFO)
    def failed(*_a, **_kw):
        raise RuntimeError("https://example.invalid/?auth=SIGNED_SECRET")
    monkeypatch.setattr(svc, "acquire_digital_loan", failed)
    try:
        receipt = svc._acquire_active_loan_automatically(loan)
        assert receipt["status"] == "retryable_error"
        logs = _local_records(caplog)
        assert any("TRACE download loan_auto decision " in log and
                   "action=acquire" in log for log in logs)
        _not_private(logs, loan)
    finally:
        svc.close()


def test_persisted_adept_attention_does_not_trace_raw_attention_code(
    tmp_path, monkeypatch, caplog,
):
    svc = _service(tmp_path)
    loan = _loan()
    caplog.set_level(logging.INFO)
    try:
        aid = _seed(svc, status="attention_required")
        svc.storage.acquisition_update_state(
            aid, "attention_required",
            error_code="SIGNED_SECRET",
            attention_code="SIGNED_SECRET",
        )
        monkeypatch.setattr(
            svc, "acquire_digital_loan",
            lambda *_a, **_kw: pytest.fail("blocked auto retry may not download"),
        )
        result = svc._acquire_active_loan_automatically(loan)
        assert result["status"] == "attention_required"
        assert result["code"] == "adept_attention_required"
        assert "SIGNED_SECRET" not in repr(result)
        logs = _local_records(caplog)
        assert any("auto-retry-blocked" in log for log in logs)
        _not_private(logs, loan)
    finally:
        svc.close()


def test_relinked_local_artifact_path_is_not_traced(
    tmp_path, monkeypatch, caplog,
):
    svc = _service(tmp_path)
    loan = _loan()
    file = tmp_path / "PRIVATE_SOURCE_PATH.epub"
    file.write_bytes(b"synthetic local")
    _seed(svc, status="complete", executor="provider",
          metadata={"path": str(file)})
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(
        facade, "inspect_artifact",
        lambda _path, _expected=None: NS(valid=True, protected=False),
    )
    monkeypatch.setattr(
        svc, "catalog_index_path",
        lambda *_a, **_kw: {"status": "indexed"},
    )
    monkeypatch.setattr(
        svc, "acquire_digital_loan",
        lambda *_a, **_kw: pytest.fail("reused local artifact must not redownload"),
    )
    try:
        receipt = svc._acquire_active_loan_automatically(loan)
        assert receipt["status"] == "unchanged"
        logs = _local_records(caplog)
        assert any("catalog-relink" in log for log in logs)
        assert "PRIVATE_SOURCE_PATH" not in "\n".join(logs)
        _not_private(logs, loan)
    finally:
        svc.close()



@pytest.mark.parametrize("prior_code", [
    "adept_error", "adept_profile_ambiguous", "adept_profile_missing",
    "kobo_adept_failed", "kobo_adept_timeout", "kobo_adept_invalid_contract",
    "adept_license_fulfilled_by_other_user",
])
def test_known_adept_remediation_codes_still_reach_repeat_loan_feedback(
    tmp_path, monkeypatch, prior_code,
):
    svc = _service(tmp_path)
    try:
        aid = _seed(svc, status="attention_required")
        svc.storage.acquisition_update_state(
            aid, "attention_required",
            attention_code=prior_code,
            error_code="adept_error",
        )
        before = svc.storage.acquisition_get(aid)
        monkeypatch.setattr(
            svc, "acquire_digital_loan",
            lambda *_a, **_kw: pytest.fail("no replay of signed ACSM"),
        )
        outcome = svc._acquire_active_loan_automatically(_loan())
        assert outcome["status"] == "attention_required"
        assert outcome["code"] == prior_code
        assert outcome["retry"] == "explicit_only"
        assert svc.storage.acquisition_get(aid) == before
    finally:
        svc.close()


def test_attention_code_projection_does_not_coerce_hostile_ledger_object():
    from cifereca.core.loan_local_preparation import _public_adept_attention_code

    class Hostile:
        def __bool__(self):
            raise AssertionError("Do not cast persisted code to boolean")
        def __str__(self):
            raise AssertionError("Do not stringify persisted code")
        def __eq__(self, other):
            raise AssertionError("Do not compare hostile code to text")

    assert _public_adept_attention_code({
        "attention_code": Hostile(), "error_code": "adept_profile_required",
    }) == "adept_profile_required"
    assert _public_adept_attention_code({
        "attention_code": "https://private.invalid/?token=SIGNED_SECRET",
        "error_code": "SIGNED_SECRET",
    }) == "adept_attention_required"
