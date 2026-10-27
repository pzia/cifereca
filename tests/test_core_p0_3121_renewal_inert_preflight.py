"""P0 #3121: a renewal preflight never evaluates provider-defined getters.

All operations use the existing synthetic Paris session; no RenewLoans POST,
inventories, ADEPT fulfillment or external requests are permitted by a getter.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.core.provider_loan_renewal import _renew_source_ready_inert
from tests.test_core_p1_3121_paris_renewal_one_post import setup


def test_normal_initialized_renewal_source_and_exact_credential(tmp_path):
    service, _account, _loan, session = setup(tmp_path)
    try:
        assert _renew_source_ready_inert(session, "card") is True
        assert _renew_source_ready_inert(session, "other-credential") is False
        session._closed = True
        assert _renew_source_ready_inert(session, "card") is False
        session._closed = False
        session.connected = False
        assert _renew_source_ready_inert(session, "card") is False
        session.connected = True
        session.descriptor = replace(
            session.descriptor,
            capabilities=replace(session.descriptor.capabilities, renew_loan=False),
        )
        assert _renew_source_ready_inert(session, "card") is False
    finally:
        service.close()


@pytest.mark.parametrize("field", [
    "capabilities", "descriptor", "provider_id", "credential_id", "connected",
])
def test_property_getter_never_runs_during_renewal_preflight(
    tmp_path, monkeypatch, field,
):
    service, _account, selected, session = setup(tmp_path)
    touched = []

    def hostile(_self):
        touched.append(field)
        raise AssertionError("provider getter must not execute")

    monkeypatch.setattr(type(session), field, property(hostile), raising=False)
    try:
        result = service.renew(selected, confirmed=True)
        assert result["status"] == "blocked"
        assert result["code"] == "loan_scope_unverified"
        assert touched == []
        assert session.reads == session.posts == []
        assert service.storage.loan_renewal_state(
            __import__("cifereca.core.provider_loan_renewal",
                       fromlist=["_source_key"])._source_key(selected)
        ) is None
    finally:
        monkeypatch.undo()
        service.close()


def test_dynamic_getattribute_refused_before_renewal_without_provider_io(
    tmp_path, monkeypatch,
):
    service, _account, selected, session = setup(tmp_path)
    touched = []
    original = type(session).__getattribute__

    def hostile(self, name):
        if name in {"connected", "capabilities", "provider_id", "credential_id"}:
            touched.append(name)
            raise AssertionError("unexpected plugin getter")
        return original(self, name)

    monkeypatch.setattr(type(session), "__getattribute__", hostile)
    try:
        result = service.renew(selected, confirmed=True)
        assert result["status"] == "blocked"
        assert result["code"] == "loan_scope_unverified"
        assert touched == []
        assert session.reads == session.posts == []
    finally:
        monkeypatch.undo()
        service.close()
