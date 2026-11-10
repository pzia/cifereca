"""#3171/#3719: passive canonical delivery ABI; no Numilog legacy worker."""
from pathlib import Path
from types import SimpleNamespace
import pytest
from cifereca.core.canonical_delivery_abi import has_complete_delivery_abi

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
    return SimpleNamespace(**{name: (lambda *_a, **_kw: None) for name in names})


@pytest.mark.parametrize("names,canonical", [
    (_V1, True), (_V2, True), (_V1 + _V2, True),
    (_V2_GUARDED, True), (_V2_RUNTIME, True),
    (_V2_GUARDED + _V2_RUNTIME, True),
    (_V1[:2], False), (_V2[:2], False),
    (_V2[:2] + (_V1[2],), False),
    (_V1[:2] + (_V2_GUARDED[2],), False),
    ((_V1[0], _V2[1], _V2[2]), False), ((), False),
])
def test_complete_same_version_canonical_abi(names, canonical):
    assert has_complete_delivery_abi(_session(names)) is canonical


def test_static_probe_does_not_run_provider_descriptors():
    class Hostile:
        @property
        def downloadable_resource_snapshot(self):
            pytest.fail("descriptor must not run during passive probe")
        @property
        def downloadable_selected_offer_snapshot(self):
            pytest.fail("descriptor must not run during passive probe")
        @property
        def downloadable_selected_delivery_candidate(self):
            pytest.fail("descriptor must not run during passive probe")
    assert has_complete_delivery_abi(Hostile()) is False


@pytest.mark.parametrize("missing", _V2_GUARDED)
def test_partial_guarded_abi_cannot_claim_single_executor(missing):
    members = tuple(name for name in _V2_GUARDED if name != missing)
    assert has_complete_delivery_abi(_session(members)) is False


def test_retired_numilog_startup_module_and_broker_dispatch_are_absent():
    root = Path(__file__).resolve().parents[1]
    assert not (root / "src/cifereca/core/numilog_materialization_startup.py").exists()
    broker_source = (root / "src/cifereca/brokers.py").read_text(encoding="utf-8")
    assert "synchronize_scheduled_numilog_materialization" not in broker_source
