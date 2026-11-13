"""Explicit synthetic Paris sessions for legacy opening-sync test scenarios.

A fake positive Loan is NOT sufficient to grant startup acquisition in #3121.
Tests which deliberately assert acquisition outcomes must wire an opted-in,
connected session with exact account and credential scope. This helper is
test-only; it never connects, authenticates or fetches a real ACSM.
"""
from __future__ import annotations

from types import SimpleNamespace

from cifereca.providers.base import ProviderCapabilities, ProviderDescriptor


def enable_synthetic_paris_opening(service) -> None:
    by_credential = {}
    for a in service.accounts:
        if a.provider_id != "paris":
            continue
        by_credential.setdefault(a.credential_id, []).append(a)

    for credential, accounts in by_credential.items():
        key = service._session_key("paris", credential)
        # Do not erase independently provided failure/partial-inventory
        # fixtures; they already have an explicit runtime session.
        if key in service._sessions:
            continue
        capabilities = ProviderCapabilities(
            loans=True, digital_download=True,
            opening_loan_auto_acquisition=True,
        )
        service._sessions[key] = SimpleNamespace(
            provider_id="paris",
            credential_id=credential,
            connected=True,
            capabilities=capabilities,
            # Declare the fixture's historical empty-ID borrower convention
            # explicitly. This must never be inferred from provider_id or
            # is_primary by Core admission.
            descriptor=ProviderDescriptor(
                id="paris", display_name="Synthetic Paris",
                description="offline opening test session",
                state="ready", capabilities=capabilities,
                existing_primary_loan_account_alias="primary",
            ),
            accounts=accounts,
            options={},
        )
