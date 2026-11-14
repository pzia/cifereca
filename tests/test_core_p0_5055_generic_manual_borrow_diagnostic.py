"""#5055: read-only manual borrowing diagnostics are provider-neutral.

Local availability is never an offer, a right, or permission to mutate.
"""
from types import SimpleNamespace as NS

import pytest

from cifereca.core.manual_library_borrow import manual_borrow_diagnostic


def _diagnose(provider="synthetic-lender-a", *, scope=True, eligible=True,
              item=None, identity="alice", override=None):
    calls = {"scope": 0, "admission": 0}

    def source_current(_service, _item, _identity):
        calls["scope"] += 1
        assert _identity == "alice"
        if override == "raise_scope":
            raise RuntimeError("private signed URL")
        return scope

    def available(_service, _item, **kwargs):
        calls["admission"] += 1
        assert kwargs["provider_id"] == provider
        assert kwargs["docbase"] == "TEST"
        assert kwargs["primary_id"] == ""
        if override == "raise_admission":
            raise RuntimeError("private credentials")
        kwargs["diagnostic"]["reason_code"] = (
            "local_action_available_provider_unknown" if eligible
            else "local_previous_attempt"
        )
        return eligible

    if item is None:
        item = NS(source_provider=provider, docbase="TEST",
                  medium="digital", source_credential="opaque")
    service = NS(current_identity=(NS(identity_id=identity) if identity else None))
    result = manual_borrow_diagnostic(
        service, item, provider_id=provider, docbase="TEST",
        primary_id="", source_current=source_current,
        available=available, result_type=NS, identity_id=identity,
    )
    return result, calls


@pytest.mark.parametrize("provider", ["synthetic-lender-a", "synthetic-lender-b"])
def test_local_admission_never_claims_provider_eligibility(provider):
    result, calls = _diagnose(provider)
    assert result == {
        "local_action_possible": True,
        "reason_code": "local_action_available_provider_unknown",
        "provider_eligibility": "unknown",
    }
    assert calls == {"scope": 1, "admission": 1}


def test_foreign_source_and_missing_identity_block_before_callbacks():
    foreign = NS(source_provider="other", docbase="TEST",
                 medium="digital", source_credential="opaque")
    result, calls = _diagnose(item=foreign)
    assert result["reason_code"] == "edition_or_source_unqualified"
    assert calls == {"scope": 0, "admission": 0}
    result, calls = _diagnose(identity="")
    assert result["reason_code"] == "identity_not_selected"
    assert calls == {"scope": 0, "admission": 0}


def test_wrong_primary_or_previous_claim_are_closed_local_outcomes():
    result, calls = _diagnose(scope=False)
    assert result["reason_code"] == "local_primary_source_binding_mismatch"
    assert not result["local_action_possible"]
    assert calls == {"scope": 1, "admission": 0}
    result, calls = _diagnose(eligible=False)
    assert result["reason_code"] == "local_previous_attempt"
    assert calls == {"scope": 1, "admission": 1}


@pytest.mark.parametrize("where", ["raise_scope", "raise_admission"])
def test_secret_exceptions_fail_closed_without_logging_or_remote_get(where):
    result, _ = _diagnose(override=where)
    assert result == {
        "local_action_possible": False,
        "reason_code": "local_admission_unavailable",
        "provider_eligibility": "unknown",
    }
    assert "private" not in str(result)


def test_shared_catalogue_row_cannot_become_manual_borrow_permission():
    shared = NS(source_provider="synthetic-lender-a", docbase="TEST",
                medium="digital", source_credential="opaque",
                metadata={"catalogue_access": "shared_read_only"})
    result, calls = _diagnose(item=shared)
    assert result["reason_code"] == "edition_or_source_unqualified"
    assert calls == {"scope": 0, "admission": 0}
