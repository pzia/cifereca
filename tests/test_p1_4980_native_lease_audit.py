"""Context audit and closed receipt tests; native integration is the E2E probe."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

from cifereca.identities.models import Identity

import pytest

PATH = Path(__file__).resolve().parents[1] / "e2e/notice_c_native_lease_audit.py"
SPEC = importlib.util.spec_from_file_location("native_lease_audit", PATH)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def context():
    return SimpleNamespace(current_identity=Identity(identity_id="A", label="Fixture A"),
                           _account_context_epoch=10, _source_context_epoch=20)


def test_exact_guard_rejects_account_aba_and_source_change_without_mutating_fixture():
    service = context()
    original_identity = service.current_identity
    calls = []

    def check():
        current = (service.current_identity.identity_id,
                   service._account_context_epoch, service._source_context_epoch)
        calls.append(current)
        return current == ("A", 10, 20)

    audit.audit_guard(service, check)
    assert calls == [
        ("A", 10, 20), ("fixture-4980-foreign-profile", 11, 20),
        ("A", 12, 20), ("A", 10, 21), ("A", 10, 20),
    ]
    assert service.current_identity is original_identity
    assert service.current_identity.identity_id == "A"
    assert service._account_context_epoch == 10 and service._source_context_epoch == 20


@pytest.mark.parametrize("guard", [
    lambda service: service.current_identity.identity_id == "A",
    lambda service: service._account_context_epoch == 10,
    lambda service: service._account_context_epoch == 10
        and service.current_identity.identity_id == "A",
    lambda service: True,
])
def test_audit_rejects_weak_guard_and_restores_context(guard):
    service = context()
    with pytest.raises(AssertionError):
        audit.audit_guard(service, lambda: guard(service))
    assert service.current_identity.identity_id == "A"
    assert service._account_context_epoch == 10 and service._source_context_epoch == 20


def test_incomplete_matrix_cannot_emit_receipt():
    report = {"checks": {}}
    native = audit.NativeLeaseAudit(None, lambda _: None, report)
    with pytest.raises(AssertionError):
        native.complete()
    native.seen = set(audit.STAGES)
    with pytest.raises(AssertionError):
        native.complete()
    native.rejected = set(audit.STAGES)
    with pytest.raises(AssertionError):
        native.complete()
    native.rebound = set(audit.STAGES)
    native.complete()
    assert report["checks"][audit.MARKER] is True
    assert report["checks"][audit.LATE_MARKER] is True
    assert report["notice_c_native_lease_audit_count"] == 12
    assert report["notice_c_native_late_aba_count"] == 12


def test_restored_requires_new_current_request_and_rejected_stage():
    native = audit.NativeLeaseAudit(None, lambda _: None, {"checks": {}})
    old = SimpleNamespace(active=False)
    new = SimpleNamespace(still_current=True)
    cover = SimpleNamespace(_request=new, _document_check=lambda: True)
    with pytest.raises(AssertionError):
        native.restored("library", "list", cover, old)
    native.rejected.add("00-notice-c-library-list-offline")
    with pytest.raises(AssertionError):
        native.restored("library", "list",
                        SimpleNamespace(_request=old), old)
    native.restored("library", "list", cover, old)
    with pytest.raises(AssertionError):
        native.restored("library", "list", cover, old)
