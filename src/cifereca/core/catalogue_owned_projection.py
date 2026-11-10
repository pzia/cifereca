"""P0 #5056/#5048: a catalogue 'owned' flag is never a personal right.

This helper performs an inert exact-key ledger lookup and is not a permission
API. Never coerce, infer, cross-bind or request provider data. Unknown and
partially typed entries do not qualify. A real purchase must still go through
the independent Core acquisition/rights guards.
"""
from __future__ import annotations

from ..models import SearchResult


def attested_catalogue_owned_keys(storage, rows, *, identity_id: str):
    if type(identity_id) is not str or not identity_id:
        return frozenset()
    providers = set()
    for row in rows:
        if (type(row) is SearchResult and row.owned is True
                and type(row.source_provider) is str and row.source_provider
                and type(row.source_credential) is str and row.source_credential
                and type(row.record_id) is str and row.record_id):
            providers.add(row.source_provider)
    approved = set()
    for provider in sorted(providers):
        try:
            ledger_rows = storage.entitlements(provider_id=provider, state="active")
            if type(ledger_rows) not in (list, tuple) or len(ledger_rows) > 100000:
                continue
            for right in ledger_rows:
                if (type(right) is dict
                        and right.get("entitlement_type") == "owned"
                        and right.get("identity_id") == identity_id
                        and type(right.get("credential_id")) is str
                        and right["credential_id"]
                        and type(right.get("item_id")) is str
                        and right["item_id"]):
                    approved.add((
                        provider, identity_id, right["credential_id"],
                        right["item_id"],
                    ))
        except Exception:
            # One broken provider/ledger lookup must not promote another
            # provider's public catalogue flag to ownership.
            continue
    return frozenset(approved)


__all__ = ["attested_catalogue_owned_keys"]
