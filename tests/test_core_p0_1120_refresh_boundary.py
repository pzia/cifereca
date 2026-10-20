"""P0 #1120: automatic loan refresh must be an explicit native bool.

No real Paris/Adobe/Kobo calls: provider acquisition is stubbed and fails
before ACSM delivery. Historical receipts, locks and signatures are unchanged.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from cifereca.plugin_api import UserFacingError
from tests.test_core_0977_adept_retry_guard import _loan, _seed, _service


class _HostileOverride:
    def __bool__(self):
        raise AssertionError("untrusted override must not be truth-tested")

    def __str__(self):
        raise AssertionError("untrusted override must not be stringified")


@pytest.mark.parametrize("override", [
    "false", "true", 1, 0, [True], {"force": True}, _HostileOverride(),
])
def test_untyped_refresh_is_not_forwarded_as_explicit_provider_reacquisition(
    tmp_path, monkeypatch, override,
):
    svc = _service(tmp_path)
    calls = []

    def unavailable(_loan, **kwargs):
        calls.append(dict(kwargs))
        raise UserFacingError("synthetic, offline", code="transport_error")

    monkeypatch.setattr(svc, "acquire_digital_loan", unavailable)
    try:
        result = svc._acquire_active_loan_automatically(_loan(), force_refresh=override)
        assert result["status"] in {"attention_required", "retryable_error"}
        assert calls == [{}]  # Never force_refresh=True, no ACSM or Adobe.
        assert len(svc.storage.acquisitions(provider_id="paris")) == 1
    finally:
        svc.close()


@pytest.mark.parametrize("override", [
    "false", 1, [True], {"force": True}, _HostileOverride(),
])
def test_untyped_refresh_does_not_resume_one_use_adept_attention(
    tmp_path, monkeypatch, override,
):
    svc = _service(tmp_path)
    try:
        acquisition_id = _seed(svc)
        before = svc.storage.acquisition_get(acquisition_id)

        def forbidden(*_args, **_kwargs):
            pytest.fail("untyped refresh must not retry provider or ADEPT")

        monkeypatch.setattr(svc, "acquire_digital_loan", forbidden)
        result = svc._acquire_active_loan_automatically(
            _loan(), force_refresh=override,
        )
        assert result["status"] == "attention_required"
        assert result["reason"] == "persisted_adept_attention"
        assert result["retry"] == "explicit_only"
        assert svc.storage.acquisition_get(acquisition_id) == before
    finally:
        svc.close()


def test_native_true_still_triggers_explicit_refresh_under_same_guard(
    tmp_path, monkeypatch,
):
    svc = _service(tmp_path)
    calls = []

    def unavailable(_loan, **kwargs):
        calls.append(dict(kwargs))
        raise UserFacingError("synthetic, offline", code="transport_error")

    monkeypatch.setattr(svc, "acquire_digital_loan", unavailable)
    try:
        result = svc._acquire_active_loan_automatically(
            _loan(), force_refresh=True,
        )
        assert result["status"] in {"attention_required", "retryable_error"}
        assert calls == [{"force_refresh": True}]
    finally:
        svc.close()


def test_direct_guarded_entrypoint_also_normalizes_without_lock_changes(
    tmp_path, monkeypatch,
):
    import cifereca.services as services
    from types import SimpleNamespace as NS

    svc = _service(tmp_path)
    seen = []

    def no_acquisition(self, loan, **options):
        seen.append(options["force_refresh"])
        return NS(outcome={"status": "unchanged"}, previous_state=None, decision=None)

    monkeypatch.setattr(
        services.loan_local_preparation, "prepare", no_acquisition,
    )
    try:
        assert svc._acquire_active_loan_automatically_guarded(
            _loan(), force_refresh=_HostileOverride(),
        ) == {"status": "unchanged"}
        assert svc._acquire_active_loan_automatically_guarded(
            _loan(), force_refresh=True,
        ) == {"status": "unchanged"}
        assert seen == [False, True]
    finally:
        svc.close()


def test_boundary_is_before_local_decision_and_provider_delivery():
    source = (Path(__file__).resolve().parents[1] / "src/cifereca/services.py")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == "LibraryService")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef)
                  and node.name == "_acquire_active_loan_automatically_guarded")
    statements = list(method.body)
    canonical = next(node for node in statements
                     if isinstance(node, ast.Assign)
                     and any(isinstance(target, ast.Name)
                             and target.id == "force_refresh" for target in node.targets))
    assert ast.unparse(canonical.value) == "force_refresh is True"
    expressions = ast.unparse(method)
    assert canonical.lineno < next(node.lineno for node in ast.walk(method)
                                   if isinstance(node, ast.Call) and
                                   ast.unparse(node.func) == "loan_local_preparation.prepare")
    assert "loan_provider_delivery.acquire_provider_loan(" in expressions
    assert "InterProcessLock" in source.read_text(encoding="utf-8")
