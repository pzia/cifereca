"""Core-neutral search session selection by exact local account ownership.

A selected catalogue session is not acquisition permission. Preferences
only break ties among credentials attached to the CURRENT identity; they
must never promote a foreign account over a locally owned account.
"""
from __future__ import annotations

from collections.abc import Mapping


def choose_search_sessions(service, sessions, identity, *,
                           preferred_credentials=None):
    """Select one session per provider with a stable first-connected tie."""
    active = getattr(identity, "identity_id", None)
    preferences = preferred_credentials if isinstance(
        preferred_credentials, Mapping,
    ) else {}
    chosen = {}
    ranks = {}
    for session in sessions:
        provider = session.provider_id
        # A full-rank local preferred session cannot be replaced by a foreign
        # session, nor by another ordinary local account on later iterations.
        if ranks.get(provider) == 3:
            continue
        credential = getattr(session, "credential_id", None)
        is_active = False
        if (type(active) is str and active
                and type(credential) is str and credential):
            try:
                owner = service.storage.identity_for_connection(
                    provider, credential,
                )
                is_active = owner == active
            except (AttributeError, LookupError, TypeError, ValueError):
                # Unknown owner is never a local owner or a preferred one.
                pass
        rank = 2 if is_active else 1
        if (is_active and type(provider) is str
                and type(credential) is str
                and type(preferences.get(provider)) is str
                and credential == preferences[provider]):
            rank = 3
        if rank > ranks.get(provider, 0):
            chosen[provider] = session
            ranks[provider] = rank
    return list(chosen.values())
