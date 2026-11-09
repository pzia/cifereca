"""Fail-closed Numilog authenticated inventory shape adapter (#1905).

This requires the caller to attest the private session and credential before
considering the rows; a public catalogue result is never rights evidence.
"""
from __future__ import annotations

from typing import Any

from .numilog_manifest_rights import _scoped_record_id
from .private_inventory_row_scope import (
    MAX_PRIVATE_ROWS as _MAX_PRIVATE_ROWS,
    private_inventory_rows_match_scope,
)


def private_library_rows_valid(rows: list[Any], credential_id: str) -> bool:
    """Reject the complete batch on a single foreign or ambiguous Numilog row."""
    return private_inventory_rows_match_scope(
        rows, provider_id="numilog", credential_id=credential_id,
        collection="library", scenario="NUMILOG_LIBRARY",
        scoped_record_valid=lambda record_id: (
            _scoped_record_id(record_id, credential_id) is not None
        ),
    )
