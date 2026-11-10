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
def test_legacy_startup_is_excluded_by_complete_v1_or_v2_contract(
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
    calls = []
    def binding(_broker, sid):
        calls.append(("binding", sid))
        return (account, session, ())
    def consent(_broker, sid):
        calls.append(("consent", sid))
        return "on_start"
    monkeypatch.setattr(startup, "_binding", binding)
    monkeypatch.setattr(startup, "mode", consent)
    allowed = startup.eligible_source_ids(broker, "owner")
    assert allowed == (() if abi else ("sync:synthetic",))
    assert ("binding", "sync:synthetic") in calls
    assert ("consent", "sync:synthetic") in calls


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
    monkeypatch.setattr(startup, "_binding", lambda *_a: (account, session, ()))
    monkeypatch.setattr(startup, "mode", lambda *_a: "on_start")
    assert startup.eligible_source_ids(broker, "owner") == ()
    result = startup.execute(broker, "owner", "startup")
    assert result.ok is True and result.facts["status"] == "not_due"
    assert result.facts["attempted"] == 0


@pytest.mark.parametrize("missing", _V2_GUARDED)
def test_partial_guarded_abi_cannot_claim_single_executor(missing):
    members = tuple(name for name in _V2_GUARDED if name != missing)
    assert startup._canonical_source_delivery_active(_session(members)) is False
