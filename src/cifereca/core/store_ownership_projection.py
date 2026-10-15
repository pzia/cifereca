"""Purchase/possession evidence in a *store*, distinct from library loans.

A personal store's owned remote inventory is not a loan and is not evidence
that an EPUB/ACSM already exists locally. This projection only reads persisted
records and source bindings. It returns COUNTS, never private item metadata.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable


_STORE_PROVIDERS = frozenset({"kobo", "numilog"})


def owned_store_inventory_counts(storage: Any, identity_id: str, accounts: Iterable[Any]) -> dict[str, int]:
    """Count only currently attested owned rights in the given identity.

    Kobo: active owned entitlements (persisted personal manifest).
    Numilog: active owned entitlement *or* private owned catalog row from the
    account's personal library. Public search/remote catalog claims cannot
    inflate possession. Different store accounts remain separate; repeated
    projections of the same product/credential count once.
    """
    bindings: Counter[tuple[str, str]] = Counter()
    for account in accounts:
        provider = getattr(account, "provider_id", None)
        credential = getattr(account, "credential_id", None)
        owner = getattr(account, "identity_id", None)
        if (
            provider in _STORE_PROVIDERS and type(credential) is str and credential
            and owner == identity_id
        ):
            bindings[(provider, credential)] += 1
    eligible: set[tuple[str, str]] = set()
    for (provider, credential), count in bindings.items():
        if count != 1:
            continue  # one connection, one unambiguous account
        try:
            if storage.identity_for_connection(provider, credential) == identity_id:
                eligible.add((provider, credential))
        except Exception:
            continue

    rights: set[tuple[str, str, str]] = set()
    for right in storage.entitlements(state="active"):
        if not isinstance(right, dict):
            continue
        provider, credential = right.get("provider_id"), right.get("credential_id")
        product = right.get("item_id")
        if (
            (provider, credential) in eligible
            and right.get("identity_id") == identity_id
            and right.get("entitlement_type") == "owned"
            and type(product) is str and product
        ):
            rights.add((provider, credential, product))
    # Numilog currently mirrors purchases to local catalog only (#1862);
    # remote_owned must come from its private library, not generic search.
    for row in storage.catalog_entries():
        if not isinstance(row, dict) or row.get("provider_id") != "numilog":
            continue
        if (
            row.get("identity_id") != identity_id
            or row.get("local_state") != "remote_owned"
        ):
            continue
        metadata = row.get("metadata")
        if not isinstance(metadata, dict) or metadata.get("source_collection") != "library":
            continue
        credential = metadata.get("source_credential")
        product = row.get("item_id")
        if (
            ("numilog", credential) in eligible
            and type(product) is str and product
        ):
            rights.add(("numilog", credential, product))
    return {
        "owned_store_books": len(rights),
        "owned_store_sources": len(eligible),
    }
