"""Provider fetch and final local index stages of automatic digital loans.

Behaviour-preserving #1120 extraction. The caller still owns its retry guard,
original identity/entitlement, ledger, ACSM/ADEPT fulfillment and exception
handling. Dependency seams are injected for historical service monkeypatches.
No background work, provider network, or profile change is introduced.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .loan_safe_trace import loan_trace_ref


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


def index_local_loan_artifact(
    service: Any, loan: Any, identity: Any, final_path: Any, *,
    time: Any, _TRACE_LOG: Any, inspect_artifact: Any,
    ProviderConfigurationError: type[Exception],
) -> tuple[Any, dict]:
    trace_ref = loan_trace_ref(loan, identity.identity_id)
    if final_path is None or not isinstance(final_path, Path) or not final_path.is_file():
        raise ProviderConfigurationError("L'acquisition automatique n'a produit aucun artefact local")
    # The catalog's no-follow/owner checks happen *after* this inspection.
    # Reject a stable symlink at the handoff too, before the inspector reads
    # an unrelated file from a provider-controlled or rebound local path.
    if _has_symlink_component(final_path):
        raise ProviderConfigurationError("Artefact local invalide")
    final_inspection = inspect_artifact(final_path)
    if final_path.suffix.casefold() in {".epub", ".pdf"} and not final_inspection.valid:
        raise ProviderConfigurationError("Artefact local invalide")
    _TRACE_LOG.info(
        "TRACE download artifact ready loan_ref=%s bytes=%s format=%s protected=%s",
        trace_ref, final_path.stat().st_size,
        final_inspection.media_format if type(final_inspection.media_format) is str and final_inspection.media_format in {"epub", "pdf", "kepub", "acsm"} else "unknown",
        final_inspection.protected if type(final_inspection.protected) is bool else None,
    )
    index_started = time.monotonic()
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
    return final_inspection, scan
