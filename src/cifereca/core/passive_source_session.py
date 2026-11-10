"""Inert session coordinates for read-only private source admission.

A provider property cannot establish its own identity, connectivity or
capability through arbitrary Python getters. This is a necessary scope fence,
not proof of ownership or permission to perform a provider request.
"""
from __future__ import annotations

from inspect import getattr_static
from types import SimpleNamespace
from typing import Any

from ..providers.base import ProviderCapabilities, ProviderDescriptor, ProviderSession
from .loan_action_scope import _inert_session_state


def passive_private_library_session(session: Any) -> tuple[str, str, bool, bool] | None:
    """Return static provider/credential/connected/personal_library coordinates.

    Real ProviderSession subclasses use initialized memory and the exact base
    capability descriptor. Only the exact built-in SimpleNamespace is accepted
    for inert legacy tests; arbitrary plugin objects and getters fail closed.
    """
    klass = type(session)
    if issubclass(klass, ProviderSession):
        state = _inert_session_state(session)
        if (state is None or getattr_static(klass, "capabilities", None)
                is not ProviderSession.__dict__["capabilities"]):
            return None
        descriptor = state.get("descriptor")
        if type(descriptor) is not ProviderDescriptor:
            return None
        capabilities = descriptor.capabilities
        if type(capabilities) is not ProviderCapabilities:
            return None
        provider = state.get("provider_id")
        credential = state.get("credential_id")
        connected = state.get("connected")
        personal_library = capabilities.personal_library
    elif klass is SimpleNamespace:
        state = object.__getattribute__(session, "__dict__")
        capabilities = state.get("capabilities")
        if type(capabilities) is not SimpleNamespace:
            return None
        provider = state.get("provider_id")
        credential = state.get("credential_id")
        connected = state.get("connected")
        personal_library = object.__getattribute__(capabilities, "__dict__").get(
            "personal_library"
        )
    else:
        return None
    if (type(provider) is not str or not provider
            or type(credential) is not str or not credential
            or type(connected) is not bool
            or type(personal_library) is not bool):
        return None
    return provider, credential, connected, personal_library


def passive_direct_download_capable(session: Any) -> bool:
    """Inspect declared direct_download without invoking plugin properties.

    This is a necessary capability prerequisite for an existing private
    acquisition route, not consent, an attested entitlement or a GET grant.
    """
    klass = type(session)
    if issubclass(klass, ProviderSession):
        state = _inert_session_state(session)
        if (state is None or getattr_static(klass, "capabilities", None)
                is not ProviderSession.__dict__["capabilities"]):
            return False
        descriptor = state.get("descriptor")
        if type(descriptor) is not ProviderDescriptor:
            return False
        caps = descriptor.capabilities
        return (type(caps) is ProviderCapabilities
                and caps.direct_download is True)
    if klass is SimpleNamespace:
        state = object.__getattribute__(session, "__dict__")
        if type(state) is not dict:
            return False
        caps = state.get("capabilities")
        return (type(caps) is SimpleNamespace
                and object.__getattribute__(caps, "__dict__").get(
                    "direct_download"
                ) is True)
    return False


__all__ = ["passive_private_library_session", "passive_direct_download_capable"]
