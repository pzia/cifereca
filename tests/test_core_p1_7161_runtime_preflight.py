"""#7161 negative-only capability gate, no real vendor or stored user data."""
from types import SimpleNamespace as NS

import pytest

from cifereca.core.adept_runtime_preflight import require_fresh_acsm_capability


def fixture(*, outcome=True, replace=False, revoke=False, missing=False):
    calls = []
    current = [True]
    manager = NS(registry_generation=3)
    runtime = NS()
    # A class method is part of the declared runtime ABI; a standalone
    # SimpleNamespace callable is also accepted by getattr_static.
    def preflight(name):
        calls.append(("preflight", name))
        if replace:
            manager.registry_generation += 1
        if revoke:
            current[0] = False
        if isinstance(outcome, Exception):
            raise outcome
        return outcome
    if not missing:
        runtime.preflight_acsm_profile = preflight
    def required(key):
        calls.append(("service", key))
        assert key == "runtime.kobo-adept"
        return runtime
    service = NS(_plugin_manager=manager, _required_plugin_service=required)
    def scope_current():
        calls.append(("lease", None))
        return current[0]
    return service, scope_current, calls


def test_current_profile_checks_fresh_runtime_and_scope_twice():
    service, lease, calls = fixture()
    assert require_fresh_acsm_capability(
        service, "reader-a", scope_current=lease,
    ) is None
    assert calls == [
        ("lease", None),
        ("service", "runtime.kobo-adept"),
        ("preflight", "reader-a"),
        ("lease", None),
        ("service", "runtime.kobo-adept"),
    ]


@pytest.mark.parametrize("scenario,kwargs", [
    ("negative", {"outcome": False}),
    ("non_boolean", {"outcome": 1}),
    ("missing_method", {"missing": True}),
    ("runtime_failure", {"outcome": RuntimeError("private token")}),
    ("rotated_registry", {"replace": True}),
    ("revoked_scope", {"revoke": True}),
])
def test_unavailable_or_revoked_never_grants_capability(scenario, kwargs):
    service, lease, calls = fixture(**kwargs)
    with pytest.raises(ValueError, match="^canonical_adept_preflight_unavailable$"):
        require_fresh_acsm_capability(service, "reader-a", scope_current=lease)
    assert all("private token" not in str(row) for row in calls)


@pytest.mark.parametrize("name", ["", " a", "-x", "a\n", "x" * 129, None])
def test_malformed_cli_name_is_refused_without_runtime_call(name):
    service, lease, calls = fixture()
    with pytest.raises(ValueError, match="^canonical_adept_preflight_invalid$"):
        require_fresh_acsm_capability(service, name, scope_current=lease)
    assert calls == []


def test_initially_revoked_scope_does_not_call_runtime():
    service, lease, calls = fixture()
    with pytest.raises(ValueError, match="^canonical_adept_preflight_unavailable$"):
        require_fresh_acsm_capability(
            service, "reader-a", scope_current=lambda: False,
        )
    assert calls == []
