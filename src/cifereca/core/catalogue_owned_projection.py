"""P0 #5056: generic personal-ledger witness for descriptive catalogue mirroring.

No provider search flag is a right. Existing, active owned entitlement is
required for every provider and exact identity/credential/edition. Account
and revision are additionally checked when the descriptive row contains them.
This is never the stricter actionable account/epoch proof of #5073.
"""
from __future__ import annotations
from collections import defaultdict
from ..models import SearchResult
from .search_owned_evidence import _coordinate

_PUBLIC = frozenset(("catalog", "public_catalog", "commercial_catalog"))


def _eligible(row):
    return (
        type(row) is SearchResult and row.owned is True
        and all(_coordinate(v) for v in (
            row.source_provider, row.source_credential, row.record_id))
        and row.source_credential != "__public__"
        and row.source_collection not in _PUBLIC
        and (row.source_account_id is None or _coordinate(row.source_account_id))
        and (row.revision_id is None or _coordinate(row.revision_id))
        and not (type(row.metadata) is dict
                 and row.metadata.get("search_provenance") == "known_catalog_stale")
    )


def attested_catalogue_owned_keys(storage, rows, *, identity_id: str):
    if not _coordinate(identity_id):
        return frozenset()
    providers = defaultdict(list)
    for row in rows:
        if _eligible(row):
            providers[row.source_provider].append(row)
    approved = set()
    for provider in sorted(providers):
        try:
            rights = storage.entitlements(provider_id=provider, state="active")
            if type(rights) not in (list, tuple) or len(rights) > 100000:
                continue
            known = defaultdict(list)
            malformed = False
            for right in rights:
                if type(right) is not dict:
                    malformed = True
                    break
                if (right.get("state") != "active"
                        or right.get("entitlement_type") != "owned"
                        or right.get("provider_id") != provider
                        or right.get("identity_id") != identity_id
                        or not _coordinate(right.get("credential_id"))
                        or not _coordinate(right.get("item_id"))
                        or not _coordinate(right.get("account_id"))
                        or (right.get("revision_id") is not None
                            and not _coordinate(right.get("revision_id")))):
                    continue
                known[(right["credential_id"], right["item_id"])].append(right)
            if malformed:
                continue
            for row in providers[provider]:
                witnesses = known.get((row.source_credential, row.record_id), ())
                if any(
                    (row.source_account_id is None
                     or right["account_id"] == row.source_account_id)
                    and (row.revision_id is None
                         or (right.get("revision_id") or "") == row.revision_id)
                    for right in witnesses
                ):
                    approved.add((
                        provider, identity_id, row.source_credential,
                        row.source_account_id or "", row.record_id,
                        row.revision_id or "",
                    ))
        except Exception:
            continue
    return frozenset(approved)


__all__ = ["attested_catalogue_owned_keys"]
