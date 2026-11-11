"""Provider-neutral shape check for an already-authenticated private batch.

This deliberately does not authenticate a provider or prove ownership.
The plugin acquisition boundary must first attest the exact credential and
private inventory source; a public catalogue cannot turn owned=True into
a right by calling this helper.
"""
from __future__ import annotations

from inspect import getattr_static

from ..models import SearchResult

MAX_PRIVATE_ROWS = 100_000


def private_inventory_rows_match_scope(
    rows: object, *, provider_id: str, credential_id: str,
    collection: str, scenario: str, scoped_record_valid,
) -> bool:
    """Reject a whole batch if one row contradicts its attested source scope."""
    if (type(provider_id) is not str or not provider_id
            or type(credential_id) is not str or not credential_id
            or type(collection) is not str or not collection
            or type(scenario) is not str or not scenario
            or not callable(scoped_record_valid)
            or type(rows) is not list or len(rows) > MAX_PRIVATE_ROWS):
        return False
    try:
        return all(
            isinstance(row, SearchResult)
            and row.source_provider == provider_id
            and row.source_credential == credential_id
            and row.source_collection == collection
            and row.scenario == scenario
            and row.owned is True
            and row.id == row.record_id
            and scoped_record_valid(row.record_id) is True
            for row in rows
        )
    except Exception:
        return False



def credential_scoped_private_inventory_rows_valid(
    rows: object, *, provider_id: str, credential_id: str,
    collection: str, scenario: str,
) -> bool:
    """Structural provenance check; no provider-specific rights or I/O.

    A provider account/session must already have been independently bound
    before this purely descriptive, atomic batch check is used.
    """
    from .credential_scoped_record import exact_credential_scoped_record
    return private_inventory_rows_match_scope(
        rows, provider_id=provider_id, credential_id=credential_id,
        collection=collection, scenario=scenario,
        scoped_record_valid=lambda item: (
            exact_credential_scoped_record(
                item, provider_id=provider_id, credential_id=credential_id,
            ) is not None
        ),
    )


def declared_private_inventory_batch_valid(
    rows: object, *, session: object, provider_id: str, credential_id: str,
) -> bool:
    """Validate a provider-declared private-record shape without vendor logic.

    A selected-purchase producer must declare its shape explicitly. Missing,
    malformed or unknown declarations fail closed, rather than downgrading to
    a permissive legacy mirror. Providers without selected-purchase support
    retain their existing independent inventory contracts.

    This is structural metadata only: it never attests ownership or an offer.
    """
    try:
        branch = getattr_static(session, "selected_purchase_branch", None)
        contract = getattr_static(session, "private_inventory_row_contract", None)
        if branch is None and contract is None:
            return True
        if (type(contract) is not tuple or len(contract) != 3
                or type(branch) is not str or not branch
                or type(provider_id) is not str or not provider_id
                or type(credential_id) is not str or not credential_id):
            return False
        collection, scenario, record_schema = contract
        if (type(collection) is not str or not collection
                or type(scenario) is not str or not scenario
                or record_schema != "credential_scoped_record_v1"):
            return False
        return credential_scoped_private_inventory_rows_valid(
            rows, provider_id=provider_id, credential_id=credential_id,
            collection=collection, scenario=scenario,
        )
    except Exception:
        return False
