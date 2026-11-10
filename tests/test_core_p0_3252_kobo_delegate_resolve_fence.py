"""#3252: Core never calls provider.resolve_acquisition(delegated) without proof.

Synthetic Kobo/Runtime services only. The provider's own bearer binding and
scope probe are independently tested in provider-kobo/tests.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.kobo.executor import _provider_delegation_admitted
from cifereca.kobo.vault import KoboVaultError
from tests.test_kobo_executor_policy import (
    _candidate, _item, _service, _state,
)


@pytest.mark.parametrize("claim", [None, False, "true", 1])
def test_missing_or_malformed_provider_attestation_is_rejected(claim):
    session = NS(supports_acquisition_delegation=claim)
    assert _provider_delegation_admitted(session) is False


@pytest.mark.parametrize("probe", [None, False, True, RuntimeError])
def test_attested_provider_probe_is_required_and_fail_closed(probe):
    def current():
        if probe is RuntimeError:
            raise RuntimeError("synthetic stale bearer")
        return probe
    session = NS(
        supports_acquisition_delegation=True,
        _delegation_admitted=current,
    )
    assert _provider_delegation_admitted(session) is (probe is True)


@pytest.mark.parametrize("claim,probe", [
    (False, None), (None, None), ("true", None),
    (True, False), (True, RuntimeError),
])
def test_retired_d3_cannot_use_delegated_bearer_without_canonical_abi(
    tmp_path, monkeypatch, claim, probe,
):
    from cifereca.providers import ProviderConfigurationError
    service, session = _service(tmp_path)
    session.supports_acquisition_delegation = claim
    if probe is not None:
        def current():
            if probe is RuntimeError:
                raise RuntimeError("stale bearer")
            return probe
        session._delegation_admitted = current
    monkeypatch.setattr(
        session, "resolve_acquisition",
        lambda *_a, **_kw: pytest.fail("old D3 must not probe descriptor"),
    )
    try:
        with pytest.raises(ProviderConfigurationError,
                           match="kobo_legacy_direct_get_retired"):
            service.acquire_direct_item(_item())
        assert session.acquisition_modes == []
        assert service.storage.acquisitions(provider_id="kobo") == []
    finally:
        service.close()
