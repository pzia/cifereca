"""Source-neutral preflight for the legacy automatic loan facade.

A configured/connected provider session may offer a digital download but must
also declare that it supports *unattended* loan acquisition. This inert
capability check is never proof of a right, borrower, consent, or entitlement.
All subsequent Core admission, ADEPT and exact-source checks remain required.
"""
from __future__ import annotations

from inspect import getattr_static

from ..providers.base import ProviderCapabilities, ProviderDescriptor


def auto_loan_refusal(service: object, provider_id: str, credential_id: str) -> str | None:
    """Return an inert, bounded refusal before locking or supplier I/O.

    A missing source session in a real Core cannot issue a provider request;
    preserve that historical path for offline Core tests which stub the
    downloader rather than registering a provider session. A non-Core stub
    with no session registry fails closed.
    """
    sessions = getattr_static(service, "_sessions", None)
    if type(sessions) is not dict:
        return "loan_auto_explicit_only"
    session = sessions.get(f"{provider_id.lower()}::{credential_id}")
    if session is None:
        return None
    descriptor = getattr_static(session, "descriptor", None)
    if (type(descriptor) is not ProviderDescriptor
            or descriptor.id != provider_id or descriptor.state != "ready"
            or type(descriptor.capabilities) is not ProviderCapabilities
            or descriptor.capabilities.opening_loan_auto_acquisition is not True
            or descriptor.capabilities.digital_download is not True
            or descriptor.capabilities.loans is not True
            or getattr_static(session, "provider_id", None) != provider_id
            or getattr_static(session, "credential_id", None) != credential_id
            or getattr_static(session, "connected", None) is not True):
        return "loan_auto_explicit_only"
    return None
