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
def test_no_delegated_resolver_call_when_session_lacks_current_bearer_grant(
    tmp_path, claim, probe,
):
    def descriptor(product, revision, mode):
        return {
            "provider": "kobo", "schema_version": "1.4",
            "acquisition": {
                "product_id": product, "revision_id": revision,
                "mode": mode, "validity": {},
                "candidates": [_candidate(
                    url="https://cdn.invalid/kdrm",
                    media_format="kepub", source_format="KEPUB",
                    handling="kobo_kdrm", drm="KDRM",
                )],
            },
        }

    service, session = _service(tmp_path, payload_factory=descriptor)
    session.supports_acquisition_delegation = claim
    if probe is not None:
        def current():
            if probe is RuntimeError:
                raise RuntimeError("synthetic session changed")
            return probe
        session._delegation_admitted = current
    service.kobo_adept_state = lambda **_kw: _state(acsm=False, delegated=True)
    service.resolve_adept_profile = lambda **_kw: ("", "none")
    try:
        with pytest.raises(KoboVaultError) as error:
            service.acquire_direct_item(_item())
        assert error.value.code == "missing_delegation_scope"
        assert error.value.status_code == 403
        assert session.acquisition_modes
        assert set(session.acquisition_modes) == {"descriptor"}
        assert session._cifereca_delegation_attempts_saved >= 1
    finally:
        service.close()
