"""Fail-closed boundary for authenticated Numilog inventory rows (#1905).

The connected source and every projected record must agree on the exact
credential, private collection and purchased variant. A public catalogue
search or another account's rows can never be relabelled as this library.
The check is purely local and cannot download or resolve ACSM links.
"""
from __future__ import annotations

from typing import Any

from ..models import SearchResult
from .numilog_manifest_rights import _scoped_record_id

_MAX_PRIVATE_ROWS = 100_000


def private_library_rows_valid(rows: list[Any], credential_id: str) -> bool:
    """Reject the complete batch on a single foreign or ambiguous row."""
    if (
        type(credential_id) is not str or not credential_id
        or type(rows) is not list or len(rows) > _MAX_PRIVATE_ROWS
    ):
        return False
    return all(
        isinstance(row, SearchResult)
        and row.source_provider == "numilog"
        and row.source_credential == credential_id
        and row.source_collection == "library"
        and row.scenario == "NUMILOG_LIBRARY"
        and row.owned is True
        and row.id == row.record_id
        and _scoped_record_id(row.record_id, credential_id) is not None
        for row in rows
    )
