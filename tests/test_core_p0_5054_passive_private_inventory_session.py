"""P0 #5054: private inventory does not authorize plugin property execution."""
from types import SimpleNamespace as NS

import pytest

from cifereca.core.passive_source_session import passive_private_library_session
from cifereca.core.numilog_manifest_rights import numilog_inventory_binding_valid
from cifereca.providers.base import (
    ProviderSession, ProviderDescriptor, ProviderCapabilities,
)
from tests.test_numilog_live_session_binding import source


class SyntheticSession(ProviderSession):
    def connect(self):
        return []

    def close(self):
        return None


def session():
    descriptor = ProviderDescriptor(
        id="numilog", display_name="Synthetic", description="",
        state="ready",
        capabilities=ProviderCapabilities(personal_library=True),
    )
    current = SyntheticSession(
        descriptor=descriptor, credential_id="secret-ref",
        label="Synthetic", credentials={}, options={},
    )
    current.connected = True
    return current


def test_real_provider_session_reads_initialized_fields_only():
    current = session()
    assert passive_private_library_session(current) == (
        "numilog", "secret-ref", True, True,
    )
    current.connected = False
    assert passive_private_library_session(current) == (
        "numilog", "secret-ref", False, True,
    )


@pytest.mark.parametrize("field", ["connected", "capabilities"])
def test_provider_property_override_is_denied_without_invocation(monkeypatch, field):
    current = session()
    calls = []

    def malicious(_session):
        calls.append("plugin getter")
        raise RuntimeError("unexpected private network I/O")

    with monkeypatch.context() as m:
        m.setattr(SyntheticSession, field, property(malicious), raising=False)
        assert passive_private_library_session(current) is None
    assert calls == []


def test_forged_dynamic_session_and_unknown_capability_are_not_evidence():
    class Proxy:
        def __getattribute__(self, name):
            raise RuntimeError("plugin getter")

    assert passive_private_library_session(Proxy()) is None
    assert passive_private_library_session(NS(
        provider_id="numilog", credential_id="secret-ref", connected=True,
        capabilities=NS(personal_library="true"),
    )) is None


def test_numilog_inventory_binding_rejects_hostile_provider_scalar_without_getter(
    monkeypatch,
):
    service, account, _old = source()
    current = session()
    service._sessions[("numilog", "secret-ref")] = current
    assert numilog_inventory_binding_valid(service, account, session=current)
    calls = []

    def malicious(_session):
        calls.append("plugin getter")
        return "numilog"

    with monkeypatch.context() as m:
        m.setattr(SyntheticSession, "provider_id", property(malicious), raising=False)
        assert not numilog_inventory_binding_valid(service, account, session=current)
    assert calls == []
