"""#4673: Paris local borrow readiness is not supplier eligibility.

No plugin calls, HTTP, POST, accounts or private sources in this fixture.
The boolean used by the UI and diagnostics must share exactly one decision.
"""
from types import SimpleNamespace

import pytest

from cifereca.core import manual_paris_borrow as paris


class FakeResult:
    def __init__(self, *, source_provider="paris", docbase="DILICOM",
                 medium="digital", source_credential="opaque-credential"):
        self.source_provider = source_provider
        self.docbase = docbase
        self.medium = medium
        self.source_credential = source_credential


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.setattr(paris, "SearchResult", FakeResult)
    counters = {"scope": 0, "local": 0}
    choices = {"scope": True, "local": True}

    def scope(*args):
        counters["scope"] += 1
        return choices["scope"]

    def local(*args, **kwargs):
        counters["local"] += 1
        diagnostic = kwargs.pop("diagnostic", None)
        assert kwargs == {
            "provider_id": "paris", "docbase": "DILICOM",
            "primary_id": "",
        }
        if diagnostic is not None:
            diagnostic["reason_code"] = (
                "local_action_available_provider_unknown"
                if choices["local"] else "local_admission_unavailable"
            )
        return choices["local"]

    monkeypatch.setattr(paris, "_normal_paris_source_current", scope)
    monkeypatch.setattr(paris, "manual_borrow_available", local)
    service = SimpleNamespace(current_identity=SimpleNamespace(identity_id="profile-A"))
    return service, choices, counters


def test_readiness_is_not_provider_permission_even_when_local_action_possible(harness):
    service, choices, counters = harness
    item = FakeResult()
    evidence = paris.manual_paris_borrow_diagnostic(service, item)
    assert evidence == {
        "local_action_possible": True,
        "reason_code": "local_action_available_provider_unknown",
        "provider_eligibility": "unknown",
    }
    assert paris.manual_paris_borrow_available(service, item) is True
    assert counters == {"scope": 2, "local": 2}


@pytest.mark.parametrize(("change", "reason"), [
    ({"source_provider": "bnf-pret"}, "edition_or_source_unqualified"),
    ({"docbase": "CATALOGUE"}, "edition_or_source_unqualified"),
    ({"medium": "physical"}, "edition_or_source_unqualified"),
    ({"source_credential": ""}, "edition_or_source_unqualified"),
])
def test_foreign_or_ambiguous_edition_never_reaches_local_admission(harness, change, reason):
    service, choices, counters = harness
    result = paris.manual_paris_borrow_diagnostic(service, FakeResult(**change))
    assert result["local_action_possible"] is False
    assert result["provider_eligibility"] == "unknown"
    assert result["reason_code"] == reason
    assert counters == {"scope": 0, "local": 0}


def test_missing_identity_rejects_without_other_checks(harness):
    service, choices, counters = harness
    service.current_identity = None
    evidence = paris.manual_paris_borrow_diagnostic(service, FakeResult())
    assert evidence["reason_code"] == "identity_not_selected"
    assert evidence["local_action_possible"] is False
    assert counters == {"scope": 0, "local": 0}


def test_primary_connection_mismatch_stops_before_local_admission(harness):
    service, choices, counters = harness
    choices["scope"] = False
    evidence = paris.manual_paris_borrow_diagnostic(service, FakeResult())
    assert evidence["reason_code"] == "primary_paris_connection_mismatch_or_inactive"
    assert evidence["local_action_possible"] is False
    assert counters == {"scope": 1, "local": 0}


def test_local_rejection_never_claims_remote_ineligibility(harness):
    service, choices, counters = harness
    choices["local"] = False
    evidence = paris.manual_paris_borrow_diagnostic(service, FakeResult())
    assert evidence["reason_code"] == "local_admission_unavailable"
    assert evidence["provider_eligibility"] == "unknown"
    assert paris.manual_paris_borrow_available(service, FakeResult()) is False


def test_exception_fails_closed_without_leaking_private_data(harness, monkeypatch):
    service, choices, counters = harness

    def secret_failure(*args, **kwargs):
        raise RuntimeError("sensitive login and signed URL")
    monkeypatch.setattr(paris, "manual_borrow_available", secret_failure)
    evidence = paris.manual_paris_borrow_diagnostic(service, FakeResult())
    assert evidence == {
        "local_action_possible": False,
        "reason_code": "local_admission_unavailable",
        "provider_eligibility": "unknown",
    }
    assert "sensitive" not in str(evidence)
