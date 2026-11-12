"""P0 #5730: check provider-declared account cardinalities without side effects."""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable


class ProviderAccountLimitError(ValueError):
    """Closed, non-sensitive configuration refusal."""


def validate_provider_account_limits(\n    rows: object, descriptors: Iterable[object], *,\n    default_identity: str = "default", known_identities: Iterable[str] = (),\n) -> None:
    """Fail closed for configured providers whose descriptor declares a limit.

    Account scope is (provider, identity). A duplicated record for the same
    credential counts once, matching the bootstrap legacy guard. Both enabled
    and disabled configured credentials participate; this does not activate any.
    Providers absent from the registry cannot be inferred from a guessed limit.
    """
    if type(rows) not in (list, tuple):
        raise ProviderAccountLimitError("Invalid configured provider accounts")
    limits: dict[str, int] = {}
    for descriptor in descriptors:
        provider = getattr(descriptor, "id", None)
        if type(provider) is not str or not provider.strip():
            raise ProviderAccountLimitError("Invalid provider descriptor")
        provider = provider.strip().lower()
        limit = getattr(descriptor, "max_accounts_per_identity", None)
        if limit is not None:
            if type(limit) is not int or limit < 1:
                raise ProviderAccountLimitError("Invalid provider account limit")
            limits[provider] = limit
    known = set(known_identities)\n    seen: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in rows:
        if type(row) is not dict:
            continue
        provider = str(row.get("provider") or "").strip().lower()
        if provider not in limits:
            continue
        credential = str(row.get("id") or "").strip()
        if not credential:
            continue
        identity = str(row.get("identity_id") or default_identity).strip()\n        if known and identity not in known:\n            identity = default_identity
        key = (provider, identity)
        seen[key].add(credential)
        if len(seen[key]) > limits[provider]:
            # No identifiers, tokens, labels, or exception from the provider.
            raise ProviderAccountLimitError("Provider account limit exceeded")
