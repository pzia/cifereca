"""#3196: a retired Kobo executor must be physically absent from Core.

The provider owns delegated bearer checks. The Core rejects the historical
direct acquisition before consulting a session or provider manifest.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_kobo_executor_policy import (
    _candidate, _item, _service, _state,
)


def test_legacy_kobo_executor_module_is_absent():
    root = Path(__file__).resolve().parents[1]
    assert not (root / "src/cifereca/kobo/executor.py").exists()
    services = (root / "src/cifereca/services.py").read_text(encoding="utf-8")
    assert "from .kobo.executor import" not in services
    assert "return execute_kobo_acquisition(" not in services

@pytest.mark.parametrize("claim,probe", [
    (False, None), (None, None), ("true", None),
    (True, False), (True, RuntimeError),
])
def test_retired_d3_cannot_use_delegated_bearer_without_canonical_abi(
    tmp_path, monkeypatch, claim, probe,
):
    from cifereca.providers import ProviderConfigurationError
    service, session = _service(tmp_path, payload_factory=lambda *_args: pytest.fail("retired D3 must not resolve payload"))
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
                           match="canonical_explicit_action_required"):
            service.acquire_direct_item(_item())
        assert session.acquisition_modes == []
        assert service.storage.acquisitions(provider_id="kobo") == []
    finally:
        service.close()
