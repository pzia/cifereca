"""#4048 safety barrier: discontinued voluntary returns cannot mutate anything.

Synthetic Core fixture, offline. The old positive-return test matrix must be
replaced separately before this draft can merge. No provider/ADEPT I/O allowed.
"""
from __future__ import annotations

import pytest

from cifereca.brokers import AdeptLoanBrokerImpl
from cifereca.plugin_api import AttentionRequired, DrmMutationRequest
from tests.test_core_p1_3121_paris_renewal_one_post import setup


@pytest.mark.parametrize("command", [
    "return_loan_prefer_adept", "return_digital", "verify_loan_return",
])
def test_legacy_loan_return_calls_cannot_read_supplier_or_dispatch(tmp_path, command):
    service, _account, loan, session = setup(tmp_path)
    try:
        result = getattr(service, command)(loan)
        assert result["code"] == "loan_return_removed"
        assert result["status"] in {"blocked", "verification_unavailable"}
        assert result.get("retry") == "none"
        assert session.reads == []
        assert session.posts == []
    finally:
        service.close()


def test_adept_return_is_denied_before_lock_or_acquisition_lookup(tmp_path):
    service, _account, _loan, session = setup(tmp_path)
    try:
        for runner in (
            lambda: service.return_adept_acquisition("acq-existing"),
            lambda: AdeptLoanBrokerImpl(service).return_acquisition("acq-existing"),
        ):
            with pytest.raises(AttentionRequired) as err:
                runner()
            assert err.value.code == "loan_return_removed"
        assert session.reads == []
        assert session.posts == []
    finally:
        service.close()


def test_direct_legacy_drm_return_request_is_denied_without_plugin(tmp_path):
    service, _account, _loan, session = setup(tmp_path)
    try:
        request = DrmMutationRequest(
            operation="return", identity_id="default", provider_id="paris",
            loan_id="adept-synthetic",
        )
        with pytest.raises(AttentionRequired) as err:
            service.execute_drm_mutation(request)
        assert err.value.code == "loan_return_removed"
        assert session.reads == []
        assert session.posts == []
    finally:
        service.close()
