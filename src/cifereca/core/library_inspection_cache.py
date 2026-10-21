"""Single-use, Core-private handoff of a recent reconciliation inspection.

Only the redacted result crosses the plugin broker. The private case projection
is bounded by LocalInspection, consumed once by CoreUI, and is never a repair
token. An exact source-file stamp is required before reuse and on every UI read.
No SQLite connection, network access, cache file or disk mutation occurs here.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
from threading import Lock
from time import monotonic

_LOCK = Lock()
_SERIAL = 0
_SLOT = None
_TTL_SECONDS = 300


@dataclass(repr=False)
class _Slot:
    owner: object
    identity_id: str
    generation: int
    expires: float
    session: object


def _source_stamp(path: Path):
    """Metadata-only freshness guard for DB and both live SQLite sidecars.

    A missing sidecar is distinct from an unsafe or unreadable sidecar. The
    owner also enforces identity/roots/config/epoch on every private access.
    """
    if not path.is_absolute() or '..' in path.parts:
        return None
    observed = []
    for name in (str(path), str(path) + '-wal', str(path) + '-shm'):
        try:
            item = os.lstat(name)
        except FileNotFoundError:
            observed.append(None)
            continue
        except OSError:
            return None
        if not stat.S_ISREG(item.st_mode):
            return None
        observed.append((item.st_dev, item.st_ino, item.st_size,
                         item.st_mtime_ns, item.st_ctime_ns))
    return tuple(observed) if observed[0] is not None else None


def _service_stamp(service):
    try:
        return _source_stamp(Path(service.storage.path))
    except (AttributeError, TypeError, ValueError, OSError):
        return None


def begin_inspection_cache(service):
    """Invalidate the previous private report before a new public audit.

    Generation prevents a late concurrent audit from replacing a newer one.
    The file stamp precedes snapshot capture, not merely final projection.
    """
    global _SERIAL, _SLOT
    with _LOCK:
        _SERIAL += 1
        generation = _SERIAL
        old, _SLOT = _SLOT, None
    if old is not None:
        old.session.close()
    return generation, _service_stamp(service)


def remember_inspection_cache(service, identity_id, audit, scope_valid, generation, stamp):
    """Keep *only* a bounded local projection; never retain raw audit graphs.

    This is best-effort UI acceleration. Failure must not alter the public
    redacted audit outcome; a subsequent inspection simply reads afresh.
    """
    global _SLOT
    if stamp is None or not scope_valid() or _service_stamp(service) != stamp:
        return
    session = None
    try:
        # Late import keeps the Core-private projector off the public broker's
        # import path and avoids a module initialization cycle.
        from .library_local_inspection import LocalInspection
        from .library_audit_roots import configured_identity_roots
        roots = configured_identity_roots(service.config, identity_id)
        session = LocalInspection(audit, scope_valid, identity_id,
                                  roots=roots.get(identity_id, ()))
        expires = monotonic() + _TTL_SECONDS
        session._valid = lambda: (scope_valid() and monotonic() < expires
                                  and _service_stamp(service) == stamp)
        if not session.valid():
            return
        with _LOCK:
            if generation == _SERIAL and _SLOT is None:
                _SLOT = _Slot(service, identity_id, generation, expires, session)
                session = None  # ownership transferred to the one-shot slot
    except Exception:
        # Never log arbitrary source exceptions: paths, titles and account data
        # can appear in private parser or callback errors.
        pass
    finally:
        if session is not None:
            session.close()


def take_inspection_cache(service, identity_id):
    """Consume a matching private projection exactly once or fail closed."""
    global _SLOT
    with _LOCK:
        slot, _SLOT = _SLOT, None
    if slot is None:
        return None
    session = slot.session
    if (slot.owner is service and slot.identity_id == identity_id
            and monotonic() < slot.expires and session.valid()):
        return session
    session.close()
    return None
