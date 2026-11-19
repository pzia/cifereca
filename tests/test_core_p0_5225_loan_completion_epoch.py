"""#5225: refuse stale source epochs across direct loan completion callbacks."""
from types import SimpleNamespace as NS

import pytest

from cifereca import services
from tests.test_automatic_loan_admission import _setup


@pytest.mark.parametrize("stage", ["delivery", "index", "receipt"])
@pytest.mark.parametrize("change", ["loan_id", "connection_aba"])
def test_no_completed_receipt_or_cleanup_after_revocation(tmp_path, monkeypatch, stage, change):
    service, loan = _setup(tmp_path)
    candidate = tmp_path / "synthetic.pdf"
    candidate.write_bytes(b"synthetic")
    events = []

    def revoke():
        if change == "loan_id":
            loan.id = "foreign-loan"
        else:
            for enabled in (False, True):
                service.storage.provider_connection_put({
                    "provider_id": "paris", "credential_id": "source-a",
                    "identity_id": "default", "enabled": enabled,
                })

    def delivery(*args, **kwargs):
        events.append("delivery")
        if stage == "delivery":
            revoke()
        return NS(path=candidate, kind="direct", acsm_path=None)

    def index(*args, **kwargs):
        events.append("index")
        if stage == "index":
            revoke()
        return NS(media_format="pdf", protected=False), {"status": "indexed"}

    original = services.loan_completion_receipt.completed_receipt

    def receipt(**kwargs):
        events.append("receipt")
        result = original(**kwargs)
        if stage == "receipt":
            revoke()
        return result

    try:
        monkeypatch.setattr(services.loan_provider_delivery, "acquire_provider_loan", delivery)
        monkeypatch.setattr(services.loan_provider_delivery, "index_local_loan_artifact", index)
        monkeypatch.setattr(services.loan_completion_receipt, "completed_receipt", receipt)
        outcome = service._acquire_active_loan_automatically_guarded(loan)
        assert outcome == {
            "status": "attention_required", "loan_id": "loan-0",
            "code": "adept_identity_unproven", "reason": "source_scope_changed",
            "retry": "explicit_only",
        }
        assert events == ["delivery", "index", "receipt"][:["delivery", "index", "receipt"].index(stage)+1]
        rows = service.storage.acquisitions(provider_id="paris")
        assert len(rows) == 1 and rows[0]["status"] == "acquiring"
        assert candidate.read_bytes() == b"synthetic"
    finally:
        service.close()
