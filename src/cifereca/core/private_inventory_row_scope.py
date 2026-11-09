"""Provider-neutral shape check for an already-authenticated private batch.

This deliberately does not authenticate a provider or prove ownership.
The plugin acquisition boundary must first attest the exact credential and
private inventory source; a public catalogue cannot turn owned=True into
a right by calling this helper.
"""
from __future__ import annotations

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
