"""Kobo public compatibility facade for the provider-neutral canonical B relink.

This adapter fixes the exact provider and existing lock semantics. No legacy
backup authority, acquisition, GET or 0.7 writer lives in this module.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..paths import acquisition_lock_file
from .canonical_placement_relink import (
    CanonicalRelinkRejected, relink_verified_canonical,
)


def relink_verified_canonical_kobo(
    storage, *, library_root: Path, identity_id: str, credential_id: str,
    account_id: str, edition_id: str, right_id: str, branch_id: str,
    placement_id: str, new_path: Path, confirmation: str | None = None,
    reference_kind: str = "purchase",
    context_current: Callable[[], bool] | None = None,
    undo_operation: str | None = None,
) -> dict[str, object]:
    return relink_verified_canonical(
        storage, provider_id="kobo", lock_path=acquisition_lock_file(),
        library_root=library_root, identity_id=identity_id,
        credential_id=credential_id, account_id=account_id,
        edition_id=edition_id, right_id=right_id, branch_id=branch_id,
        placement_id=placement_id, new_path=new_path,
        confirmation=confirmation, reference_kind=reference_kind,
        context_current=context_current, undo_operation=undo_operation,
    )


__all__ = ["CanonicalRelinkRejected", "relink_verified_canonical_kobo"]
