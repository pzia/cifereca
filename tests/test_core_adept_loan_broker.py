from __future__ import annotations

import inspect

from cifereca.brokers import AdeptLoanBrokerImpl, build_public_brokers
from cifereca.plugin_api import AdeptLoanBroker


class Service:
    def __init__(self):
        self.calls = []

    def reconcile_adept_acquisition(self, acquisition_id, *, profile_references=None):
        self.calls.append(("reconcile", acquisition_id, profile_references))
        return {"status": "resolved", "acquisition_id": acquisition_id}

    def adept_loan_choices(self, acquisition_id, *, profile_references=None):
        self.calls.append(("choices", acquisition_id, profile_references))
        return {"options": [], "complete": True}

    def attach_adept_loan(self, acquisition_id, profile_id, loan_id):
        self.calls.append(("attach", acquisition_id, profile_id, loan_id))
        return {"status": "resolved", "acquisition_id": acquisition_id}

    def return_adept_acquisition(self, acquisition_id):
        self.calls.append(("return", acquisition_id))
        return {"ok": True, "verification": "confirmed", "artifact_preserved": True}


def test_public_broker_registry_contains_adept_loans():
    broker = build_public_brokers(Service())["adept.loans"]
    assert isinstance(broker, AdeptLoanBroker)


def test_reconcile_and_choices_forward_only_explicit_stable_profile_ids():
    service = Service()
    broker = AdeptLoanBrokerImpl(service)

    broker.reconcile("acq-1", profile_ids=("profile-a", "profile-a", "profile-b"))
    broker.choices("acq-1", profile_ids=("profile-b",))

    assert service.calls == [
        ("reconcile", "acq-1", ["profile-a", "profile-b"]),
        ("choices", "acq-1", ["profile-b"]),
    ]


def test_attach_contract_requires_explicit_adept_loan_id_and_passes_it_unchanged():
    service = Service()
    broker = AdeptLoanBrokerImpl(service)

    parameters = inspect.signature(AdeptLoanBrokerImpl.attach).parameters
    assert "adept_loan_id" in parameters
    assert "loan_id" not in parameters

    broker.attach("acq-1", "profile-stable", "urn:uuid:adept-exact")

    assert service.calls == [
        ("attach", "acq-1", "profile-stable", "urn:uuid:adept-exact"),
    ]


def test_return_is_keyed_only_by_acquisition_and_preserves_core_confirmation():
    service = Service()
    broker = AdeptLoanBrokerImpl(service)

    result = broker.return_acquisition("acq-1")

    assert service.calls == [("return", "acq-1")]
    assert result == {
        "ok": True,
        "verification": "confirmed",
        "artifact_preserved": True,
    }


def test_empty_profile_scope_keeps_core_origin_resolution():
    service = Service()
    broker = AdeptLoanBrokerImpl(service)

    broker.reconcile("acq-1")
    broker.choices("acq-1")

    assert service.calls == [
        ("reconcile", "acq-1", None),
        ("choices", "acq-1", None),
    ]
