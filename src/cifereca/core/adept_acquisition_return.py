"""Compatibility boundary for retired ADEPT loan return (#4048).

Other ADEPT operations (acquisition, inventory, origin evidence) remain in
their own services. No CLI command or historical SQLite mutation is issued.
"""
from __future__ import annotations

from typing import Any


def return_adept_acquisition(
    self, acquisition_id: str, *, AttentionRequired, ProviderConfigurationError,
    DrmMutationRequest, time,
) -> dict[str, Any]:
    """Legacy callers receive a closed error before any Core state access."""
    raise AttentionRequired(
        "Restitution volontaire retirée de Cifereca.",
        code="loan_return_removed",
    )
