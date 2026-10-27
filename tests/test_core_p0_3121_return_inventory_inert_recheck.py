"""P0 #3121: return-inventory verification never executes a provider getter.

The verification boundary is exercised against the real Core coordinator
with synthetic Paris sessions. No return POST, browser, ACSM or ADEPT call.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.core.loan_return_verification import (
    _read_paris_return_presence,
    _return_inventory_session_connected_inert,
)
from tests.test_core_0982_loan_return_reconciliation import Session, _library, _loan


def test_inert_probe_preserves_native_memory_and_legacy_namespace():
    assert _return_inventory_session_connected_inert(Session([]))
    assert _return_inventory_session_connected_inert(NS(connected=True))
    assert not _return_inventory_session_connected_inert(NS(connected=False))
    assert not _return_inventory_session_connected_inert(NS())


@pytest.mark.parametrize("kind", ["property", "dynamic"])
def test_before_inventory_provider_connected_getter_is_never_called(
    tmp_path, kind,
):
    touched = []

    if kind == "property":
        class Evil(Session):
            @property
            def connected(self):
                touched.append("GET/property")
                raise AssertionError("must remain local")
    else:
        class Evil(Session):
            def __getattribute__(self, field):
                if field == "connected":
                    touched.append("GET/dynamic")
                    raise AssertionError("must remain local")
                return super().__getattribute__(field)

    source = Evil([_loan()])
    service = _library(tmp_path, session=source)
    try:
        with pytest.raises(ValueError, match="return_inventory_source_changed"):
            _read_paris_return_presence(service, service.accounts[1], source, _loan())
        assert touched == []
        assert source.checks == [] and source.posts == []
    finally:
        service.close()


@pytest.mark.parametrize("kind", ["property", "dynamic"])
def test_inflight_provider_rebinding_to_getter_cannot_verify_return_or_do_io(
    tmp_path, kind, monkeypatch,
):
    touched = []

    class Mutable(Session):
        def loans(self, account):
            rows = super().loans(account)
            if kind == "property":
                def connected(_self):
                    touched.append("GET/property")
                    raise AssertionError("must remain local")
                monkeypatch.setattr(type(self), "connected", property(connected),
                                    raising=False)
            else:
                original = type(self).__getattribute__
                def dynamic(self, key):
                    if key == "connected":
                        touched.append("GET/dynamic")
                        raise AssertionError("must remain local")
                    return original(self, key)
                monkeypatch.setattr(type(self), "__getattribute__", dynamic)
            return rows

    source = Mutable([_loan()])
    service = _library(tmp_path, session=source)
    try:
        # Positive legacy observation is accepted only if its session remains
        # locally provable AFTER the one GET. No second provider read.
        with pytest.raises(ValueError, match="return_inventory_scope_changed"):
            _read_paris_return_presence(service, service.accounts[1], source, _loan())
        assert source.checks == ["linked-b"]
        assert source.posts == []
        assert touched == []
    finally:
        monkeypatch.undo()
        service.close()


def test_plain_legacy_positive_return_presence_is_not_regressed(tmp_path):
    loan = _loan()
    source = Session([loan])
    service = _library(tmp_path, session=source)
    try:
        assert _read_paris_return_presence(
            service, service.accounts[1], source, loan,
        ) is True
        assert source.checks == ["linked-b"]
        assert source.posts == []
    finally:
        service.close()
