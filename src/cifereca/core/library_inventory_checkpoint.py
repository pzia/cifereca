"""Private, memory-only inventory reuse across scoped audits (#2734).

Only fully verified file observations are retained. This cache never contains
SQLite rights, entitlements, acquisitions, placements or inferred provenance.
Every audit takes a fresh SQLite/WAL snapshot, reopens roots O_NOFOLLOW and
revalidates file stat fingerprints; no negative evidence is reused.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from threading import Lock
from time import monotonic

from .library_inventory import FileObservation, Inventory

_LOCK = Lock()
_SERIAL = 0
# A run of profile B must not evict A's still-valid observations. These slots
# are private and bounded by BOTH count and aggregate file observations.
_SLOTS = []
# A cancelled or stalled task cannot replace a newer checkpoint for the same
# service/identity. Cap strong references left by abandoned tasks.
_ACTIVE = {}
_TTL_SECONDS = 900
_MAX_FILES = 4096
_MAX_SLOTS = 8
_MAX_ACTIVE = 16


@dataclass(slots=True, repr=False)
class _Slot:
    service: object
    identity_id: str
    scope: tuple
    generation: int
    expires: float
    rows: dict[tuple[str, str], FileObservation]


def _same(slot, service, identity_id):
    return slot.service is service and slot.identity_id == identity_id


def begin_inventory_resume(service, identity_id: str, scope: tuple):
    """Consume only the selected identity's slot, not every profile's slot.

    A changed account epoch, root, DB, service or scope never restores old
    observations, and each slot remains single-use.
    """
    global _SERIAL
    with _LOCK:
        _SERIAL += 1
        generation = _SERIAL
        now = monotonic()
        candidate = next((
            slot for slot in _SLOTS
            if _same(slot, service, identity_id)
            and slot.scope == scope and slot.expires > now
        ), None)
        _SLOTS[:] = [
            slot for slot in _SLOTS
            if slot.expires > now and not _same(slot, service, identity_id)
        ]
        _ACTIVE[(id(service), identity_id)] = (service, generation)
        while len(_ACTIVE) > _MAX_ACTIVE:
            del _ACTIVE[next(iter(_ACTIVE))]
        return generation, deepcopy(candidate.rows) if candidate is not None else {}


def remember_inventory_resume(service, identity_id: str, scope: tuple,
                              generation: int, inventory: Inventory,
                              scope_valid) -> None:
    """Keep bounded positive evidence, even if other phases were partial."""
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
        key = (id(service), identity_id)
        active = _ACTIVE.get(key)
        if (active is None or active[0] is not service
                or active[1] != generation or not scope_valid()):
            return
        del _ACTIVE[key]
        now = monotonic()
        _SLOTS[:] = [slot for slot in _SLOTS
                    if slot.expires > now and not _same(slot, service, identity_id)]
        _SLOTS.append(_Slot(service, identity_id, scope, generation,
                            now + _TTL_SECONDS, rows))
        # If too many profiles are used, only performance is sacrificed.
        # The retained bytes never authorize an attachment or a missing file.
        while (len(_SLOTS) > _MAX_SLOTS
               or sum(len(slot.rows) for slot in _SLOTS) > _MAX_FILES):
            _SLOTS.pop(0)
