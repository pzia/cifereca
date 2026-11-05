"""Retired ADEPT acquisition return entry point (#4048).

Keep this importable name solely so historical callers receive the same closed
refusal before any registry, SQLite, file, runtime, lock or diagnostic access.
ADEPT fulfillment and read-only inventory live in their separate modules.
"""
from typing import Any


def return_adept_acquisition(
    self, acquisition_id: str, *, AttentionRequired, ProviderConfigurationError,
    DrmMutationRequest, time,
) -> dict[str, Any]:
    """Voluntary return was removed; legacy direct invocations cannot bypass Core."""
    raise AttentionRequired(
        "Restitution volontaire retirée de Cifereca.",
        code="loan_return_removed",
    )
