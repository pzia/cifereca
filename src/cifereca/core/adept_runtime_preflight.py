"""#7161: scoped, fresh ADEPT capability veto before the one-use loan marker.

This is a negative-only, read-only guard. It neither proves a borrower grant
nor authorizes another attempt. The caller must supply its original live
scope/consent/borrower lease and call this before begin_loan_adept_attempt.
"""
from __future__ import annotations

from inspect import getattr_static
from typing import Callable


class AdeptProfileUnavailable(ValueError):
    """Local profile validation refused before ACSM fulfillment."""


def require_fresh_acsm_capability(
    service: object, profile_name: str, *, scope_current: Callable[[], bool],
) -> None:
    """Fail closed if this exact live runtime cannot fetch for this CLI name.

    Never use a cached UI probe, an identity's stable UUID as the profile
    name, or an unbound runtime instance. No opaque plugin error text crosses
    this Core boundary. A successful return is NOT permission to execute DRM.
    """
    if (type(profile_name) is not str or not profile_name
            or profile_name != profile_name.strip() or profile_name.startswith("-")
            or len(profile_name) > 128
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in profile_name)
            or not callable(scope_current)):
        raise AdeptProfileUnavailable("canonical_adept_preflight_invalid")
    try:
        if scope_current() is not True:
            raise ValueError("scope_not_current")
        manager = service._plugin_manager
        if manager is None:
            raise ValueError("missing_manager")
        generation = getattr(manager, "registry_generation", None)
        runtime = service._required_plugin_service("runtime.kobo-adept")
        reader = getattr_static(runtime, "preflight_acsm_profile", None)
        if not callable(reader):
            raise ValueError("missing_preflight_contract")
        if runtime.preflight_acsm_profile(profile_name) is not True:
            raise ValueError("negative_preflight")
        if (scope_current() is not True
                or service._plugin_manager is not manager
                or getattr(manager, "registry_generation", None) != generation
                or service._required_plugin_service("runtime.kobo-adept") is not runtime):
            raise ValueError("runtime_replaced")
    except Exception:
        raise AdeptProfileUnavailable("canonical_adept_preflight_unavailable") from None
