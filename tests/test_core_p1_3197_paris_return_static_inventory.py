"""#3197: legacy Paris return-completeness cannot invoke supplier getters.

Synthetic object-only checks: no ReturnDigitalLoan, session GET or ADEPT call.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cifereca.core.loan_return_verification import (
    _paris_digital_inventory_complete,
)


def test_legacy_plain_memory_syracuse_only_keeps_existing_compatibility_rule():
    assert _paris_digital_inventory_complete(
        SimpleNamespace(last_loan_errors=[]),
    )
    assert _paris_digital_inventory_complete(
        SimpleNamespace(last_loan_errors=[{"upstream": "SYRACUSE"}]),
    )
    assert not _paris_digital_inventory_complete(
        SimpleNamespace(last_loan_errors=[{"upstream": "DILICOM"}]),
    )


@pytest.mark.parametrize("shape", [
    None, {"upstream": "SYRACUSE"}, ("SYRACUSE",),
    [{"upstream": "SYRACUSE"}] * 65,
    [{"upstream": "other"}],
])
def test_unknown_or_oversized_legacy_status_cannot_attest_digital_coverage(shape):
    assert not _paris_digital_inventory_complete(
        SimpleNamespace(last_loan_errors=shape),
    )


def test_hostile_property_is_never_invoked_by_return_verification():
    events = []
    class ProviderSessionWithGetter:
        @property
        def last_loan_errors(self):
            events.append("remote request")
            raise RuntimeError("provider call forbidden on verification")

    assert not _paris_digital_inventory_complete(ProviderSessionWithGetter())
    assert events == []


def test_dynamic_attribute_fallback_is_not_executed_for_missing_status():
    events = []
    class DynamicProviderSession:
        def __getattr__(self, key):
            events.append(key)
            raise RuntimeError("unexpected plugin resolution")
    assert not _paris_digital_inventory_complete(DynamicProviderSession())
    assert events == []


def test_custom_sequence_does_not_run_provider_iteration_or_length():
    events = []
    class HostileSequence(list):
        def __len__(self):
            events.append("len")
            raise RuntimeError("plugin invoked")
        def __iter__(self):
            events.append("iter")
            raise RuntimeError("plugin invoked")
    assert not _paris_digital_inventory_complete(
        SimpleNamespace(last_loan_errors=HostileSequence()),
    )
    assert events == []
