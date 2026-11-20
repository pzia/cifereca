"""Provider fetch and final local index stages of automatic digital loans.

Behaviour-preserving #1120 extraction. The caller still owns its retry guard,
original identity/entitlement, ledger, ACSM/ADEPT fulfillment and exception
handling. Dependency seams are injected for historical service monkeypatches.
No background work, provider network, or profile change is introduced.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import stat
from typing import Any

from .loan_safe_trace import loan_trace_ref
from .library_inventory import _open_root


def _has_symlink_component(path: Path) -> bool:
    """Stable no-follow preflight for every directory component.

    A symlink in a grandparent is no safer than the immediate parent.
    This is not an inode lock: the canonical indexer still owns the final
    no-follow path, owner and digest admission under concurrent rebinding.
    """
    try:
        return path.is_symlink() or any(
            parent.is_symlink() for parent in path.parents
        )
    except (OSError, ValueError):
        return True


def acquire_provider_loan(
    service: Any, loan: Any, *, force_refresh: bool,
    time: Any, _TRACE_LOG: Any, identity_id: str | None = None,
) -> Any:
    trace_ref = loan_trace_ref(loan, identity_id if identity_id is not None else getattr(loan, "identity_id", None))
    provider_started = time.monotonic()
    _TRACE_LOG.info("TRACE download provider begin loan_ref=%s", trace_ref)
    acquisition = service.acquire_digital_loan(loan, force_refresh=True) if force_refresh else service.acquire_digital_loan(loan)
    # Once the provider call returned, best-effort TRACE reads must not
    # reclassify a completed acquisition as a failed download. The decision,
    # artifact checks and ledger remain owned by the caller's later stages.
    try:
        raw_kind = acquisition.kind
    except Exception:
        raw_kind = None
    kind = raw_kind if type(raw_kind) is str and raw_kind in {
        "direct", "acsm", "external", "command",
    } else "unknown"
    try:
        path = acquisition.path
        # Even TRACE must not stat (follow) a provider-supplied symlink.
        # An external target's private size is not an acquisition receipt.
        artifact_bytes = (
            path.stat().st_size
            if isinstance(path, Path) and not _has_symlink_component(path)
            and path.is_file() else 0
        )
    except Exception:
        artifact_bytes = 0
    _TRACE_LOG.info(
        "TRACE download provider end loan_ref=%s kind=%s bytes=%s dt=%.3fs",
        trace_ref, kind, artifact_bytes, time.monotonic()-provider_started,
    )
    return acquisition


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_nlink, info.st_mode)


@contextmanager
def _physical_witness(path, error):
    """Pin the input across legacy path-based callbacks, not a SQL transaction."""
    parent = fd = None
    try:
        try:
            if not path.is_absolute() or ".." in path.parts:
                raise ValueError("invalid_path")
            parent = _open_root(path.parent)
            named = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISREG(named.st_mode):
                raise ValueError("not_regular")
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW |
                         os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
            source = os.fstat(fd)
            signature = _signature(source)
            if not stat.S_ISREG(source.st_mode) or signature != _signature(named):
                raise ValueError("changed")
            parent_info = os.fstat(parent)
            directory = parent_info.st_dev, parent_info.st_ino
        except FileNotFoundError:
            raise error("L'acquisition automatique n'a produit aucun artefact local") from None
        except (OSError, ValueError):
            raise error("Artefact local invalide") from None

        def current():
            fresh = None
            try:
                fresh = _open_root(path.parent)
                info = os.fstat(fresh)
                named = os.stat(path.name, dir_fd=fresh, follow_symlinks=False)
                if ((info.st_dev, info.st_ino) != directory
                        or not stat.S_ISREG(named.st_mode)
                        or _signature(named) != signature
                        or _signature(os.fstat(fd)) != signature):
                    raise ValueError("changed")
            except (OSError, ValueError):
                raise error("Artefact local invalide") from None
            finally:
                if fresh is not None:
                    os.close(fresh)

        current()
        yield source.st_size, current
    finally:
        for handle in (fd, parent):
            if handle is not None:
                os.close(handle)


def index_local_loan_artifact(
    service: Any, loan: Any, identity: Any, final_path: Any, *,
    time: Any, _TRACE_LOG: Any, inspect_artifact: Any,
    ProviderConfigurationError: type[Exception],
) -> tuple[Any, dict]:
    trace_ref = loan_trace_ref(loan, identity.identity_id)
    if final_path is None or not isinstance(final_path, Path):
        raise ProviderConfigurationError("L'acquisition automatique n'a produit aucun artefact local")
    # Reject *before* following the path for even an is_file/stat query.
    # The catalog's own no-follow/owner checks run after this inspection,
    # and remain mandatory for the physical publication decision.
    if _has_symlink_component(final_path):
        raise ProviderConfigurationError("Artefact local invalide")
    if not final_path.is_file():
        raise ProviderConfigurationError("L'acquisition automatique n'a produit aucun artefact local")
    with _physical_witness(final_path, ProviderConfigurationError) as (size, current):
        final_inspection = inspect_artifact(final_path)
        current()
        # A generic .bin/.download (or an extensionless provider output) can
        # contain completely unrecognized bytes. inspect_artifact returns
        # media_format=None, valid=False in that case; checking only recognized
        # EPUB/PDF formats previously let it reach catalog_index_path().
        # Automatic loans are not an arbitrary-file importer: no invalid result
        # may become an indexed local book, regardless of suffix or format.
        if getattr(final_inspection, "valid", None) is not True:
            raise ProviderConfigurationError("Artefact local invalide")
        _TRACE_LOG.info(
            "TRACE download artifact ready loan_ref=%s bytes=%s format=%s protected=%s",
            trace_ref, size,
            final_inspection.media_format if type(final_inspection.media_format) is str and final_inspection.media_format in {"epub", "pdf", "kepub", "acsm"} else "unknown",
            final_inspection.protected if type(final_inspection.protected) is bool else None,
        )
        index_started = time.monotonic()
        current()
        scan = service.catalog_index_path(
            final_path, title=loan.title, item_id=str(loan.record_id or loan.id),
            identity_id=identity.identity_id, provider_id=loan.source_provider,
        )
        # Only the Core indexer's native receipt can attest a completed index.
        # Never invoke a plugin-owned mapping getter or comparison to turn an
        # untrusted result into success, or serialize its failure details.
        indexed = type(scan) is dict and type(scan.get("status")) is str and scan["status"] == "indexed"
        _TRACE_LOG.info("TRACE download catalog index end loan_ref=%s indexed=%s dt=%.3fs", trace_ref, indexed, time.monotonic()-index_started)
        if not indexed:
            raise ProviderConfigurationError("Indexation locale impossible")
        # A late change refuses completion, but cannot roll back the legacy index.
        current()
        return final_inspection, scan
