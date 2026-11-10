"""#3171/#3719: Numilog v2-only canonical ABI excludes legacy startup GET.

The contract probe is passive: no source I/O, network, entitlement lookup,
download, access to signed URLs, or inference of user permission.
"""
from types import SimpleNamespace

import pytest

from cifereca.core import numilog_materialization_startup as startup


_V1 = (
    "rights_observation_snapshot",
    "rights_selected_delivery_offer_snapshot",
    "rights_selected_delivery_candidate",
)
_V2 = (
    "downloadable_resource_snapshot",
    "downloadable_selected_offer_snapshot",
    "downloadable_selected_delivery_candidate",
)
_V2_GUARDED = _V2[:2] + ("downloadable_selected_delivery_candidate_guarded",)
_V2_RUNTIME = _V2[:2] + ("downloadable_selected_delivery_candidate_runtime_guarded",)


def _session(names):
    return SimpleNamespace(**{method: (lambda *_a, **_kw: None)
                              for method in names})


@pytest.mark.parametrize("names,canonical", [
    (_V1, True),
    (_V2, True),
    (_V1 + _V2, True),
    (_V2_GUARDED, True),
    (_V2_RUNTIME, True),
    (_V2_GUARDED + _V2_RUNTIME, True),
    (_V1[:2], False),
    (_V2[:2], False),
    (_V2[:2] + (_V1[2],), False),
    (_V1[:2] + (_V2_GUARDED[2],), False),
    ((_V1[0], _V2[1], _V2[2]), False),
    ((), False),
])
def test_startup_requires_complete_same_version_canonical_abi(names, canonical):
    assert startup._canonical_source_delivery_active(_session(names)) is canonical


@pytest.mark.parametrize("abi", [_V1, _V2, _V2_GUARDED, _V2_RUNTIME, ()])
def test_retired_startup_never_uses_legacy_get_for_any_provider_contract(
    monkeypatch, abi,
):
    account = SimpleNamespace(
        provider_id="numilog", identity_id="owner", credential_id="card",
        unique_id="user",
    )
    session = _session(abi)
    service = SimpleNamespace(accounts=[account])
    broker = SimpleNamespace(
        service=service, _source_id=lambda _account: "sync:synthetic",
    )
    monkeypatch.setattr(startup, "_binding", lambda *_a: (account, session, ()), raising=False)
    monkeypatch.setattr(startup, "mode", lambda *_a: "on_start", raising=False)
    assert startup.eligible_source_ids(broker, "owner") == ()



def test_static_probe_does_not_run_provider_descriptors():
    class Hostile:
        @property
        def downloadable_resource_snapshot(self):
            pytest.fail("descriptor must not be invoked by passive source probe")
        @property
        def downloadable_selected_offer_snapshot(self):
            pytest.fail("descriptor must not be invoked by passive source probe")
        @property
        def downloadable_selected_delivery_candidate(self):
            pytest.fail("descriptor must not be invoked by passive source probe")
    assert startup._canonical_source_delivery_active(Hostile()) is False


def test_guarded_only_source_does_not_enter_legacy_startup_or_get(monkeypatch):
    account = SimpleNamespace(
        provider_id="numilog", identity_id="owner", credential_id="card",
        unique_id="user",
    )
    class GuardedOnly:
        def downloadable_resource_snapshot(self, *_a, **_k):
            pytest.fail("passive ABI selection must not fetch inventory")
        def downloadable_selected_offer_snapshot(self, *_a, **_k):
            pytest.fail("passive ABI selection must not select an offer")
        def downloadable_selected_delivery_candidate_guarded(self, *_a, **_k):
            pytest.fail("legacy worker must not start a guarded candidate GET")
        def library(self):
            pytest.fail("legacy private manifest must not be read")
        def download_direct(self, *_a, **_k):
            pytest.fail("legacy direct GET must not run")

    session = GuardedOnly()
    broker = SimpleNamespace(
        service=SimpleNamespace(accounts=[account]),
        _source_id=lambda _account: "sync:exact",
    )
    # The retired worker has no binding/GET callbacks to monkeypatch.
    # Its only output is a passive not-due or historical migration receipt.
    assert startup.eligible_source_ids(broker, "owner") == ()
    result = startup.execute(broker, "owner", "startup")
    assert result.ok is True and result.facts["status"] == "not_due"
    assert result.changed == 0
    assert result.facts["legacy_writer_active"] is False


@pytest.mark.parametrize("missing", _V2_GUARDED)
def test_partial_guarded_abi_cannot_claim_single_executor(missing):
    members = tuple(name for name in _V2_GUARDED if name != missing)
    assert startup._canonical_source_delivery_active(_session(members)) is False



def test_retired_startup_physically_contains_no_direct_get_or_manifest():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] /
              "src/cifereca/core/numilog_materialization_startup.py").read_text()
    assert "download_direct(" not in source
    assert ".library(" not in source
    assert "def same_source(" not in source
    assert "numilog_materialization_claim_startup" not in source
