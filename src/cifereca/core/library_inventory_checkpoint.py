"""Private, memory-only inventory reuse across explicitly scoped audits (#2734).

Only fully verified file observations are retained. This cache is separate
from the single-use UI inspection cache and never contains SQLite ownership,
entitlement, acquisition or placement conclusions. It confers no authority.

A newly captured SQLite/WAL snapshot is mandatory on *every* audit. Reusing
file bytes still requires a fresh O_NOFOLLOW, descriptor-based filesystem
check by library_inventory.scan_identity_roots().
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from threading import Lock
from time import monotonic

from .library_inventory import FileObservation, Inventory

_LOCK = Lock()
_SERIAL = 0
_SLOT = None
_TTL_SECONDS = 900
_MAX_FILES = 4096


@dataclass(slots=True, repr=False)
class _Slot:
    service: object
    identity_id: str
    scope: tuple
    generation: int
    expires: float
    rows: dict[tuple[str, str], FileObservation]


def begin_inventory_resume(service, identity_id: str, scope: tuple):
    """Consume a previous checkpoint and invalidate stale concurrent writers.

    Private lookup keys never enter the public broker report or diagnostics.
    A different service, identity, epoch, root or configuration cannot reuse
    observations. An old concurrent run cannot overwrite a newer run.
    """
    global _SERIAL, _SLOT
    with _LOCK:
        _SERIAL += 1
        generation = _SERIAL
        slot, _SLOT = _SLOT, None
    if (slot is not None and slot.service is service
            and slot.identity_id == identity_id and slot.scope == scope
            and monotonic() < slot.expires):
        return generation, deepcopy(slot.rows)
    return generation, {}


def remember_inventory_resume(service, identity_id: str, scope: tuple,
                              generation: int, inventory: Inventory,
                              scope_valid) -> None:
    """Retain only bounded and complete observations, even after a timeout.

    Nothing is persisted to disk. A partial inventory contributes *positive
    file observations only*, never negative/missing-file assertions. A
    future scan must re-enumerate roots and reopen every retained file.
    """
    global _SLOT
    if not scope_valid():
        return
    rows = {}
    for file in inventory.files:
        if (type(file) is not FileObservation
                or file.identity_id != identity_id
                or not file.fingerprint or file.sha256 is None
                or file.validation not in {'ok', 'pdf_signature_only'}
                or file.structure_validation not in {
                    'epub_structure_crc_ok', 'pdf_structure_minimal',
                }):
            continue
        key = (file.identity_id, file.path)
        if key in rows or len(rows) >= _MAX_FILES:
            return
        rows[key] = deepcopy(file)
    with _LOCK:
        if generation == _SERIAL and scope_valid():
            _SLOT = _Slot(service, identity_id, scope, generation,
                          monotonic() + _TTL_SECONDS, rows)
