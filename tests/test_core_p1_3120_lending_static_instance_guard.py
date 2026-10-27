"""#3120: capability discovery must not call a provider's dynamic __class__.

The production assessor is invoked with synthetic sessions; no HTTP, loan
action, account refresh or plugin connect happens during inspection.
"""
from __future__ import annotations

from cifereca.plugin_api.lending_capabilities import assess_lending_operation
from tests.test_core_p1_3120_lending_capabilities import (
    OnlyList, account, descriptor,
)


def test_dynamic_instance_getattribute_never_runs_for_lending_readiness(monkeypatch):
    row = account()
    desc = descriptor()
    session = OnlyList(desc, row)
    calls = []

    def provider_getattribute(_self, name):
        calls.append(name)
        raise AssertionError("provider getter must not run during assessment")

    monkeypatch.setattr(type(session), "__getattribute__", provider_getattribute)
    result = assess_lending_operation(
        "loan.list", manifest_capabilities=("sync.loans",),
        descriptor=desc, session=session, account=row,
    )
    assert result.availability == "unavailable"
    assert result.reason_code == "session_unavailable"
    assert not result.usable
    assert calls == []


def test_normal_instance_memory_still_attests_static_session_readiness():
    row = account()
    desc = descriptor()
    session = OnlyList(desc, row)
    result = assess_lending_operation(
        "loan.list", manifest_capabilities=("sync.loans",),
        descriptor=desc, session=session, account=row,
    )
    assert result.availability == "supported"
    assert result.reason_code == "available_unverified_permission"
    assert result.session_ready
