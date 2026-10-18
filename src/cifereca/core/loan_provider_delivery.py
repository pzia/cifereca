"""Provider fetch and final local index stages of automatic digital loans.

Behaviour-preserving #1120 extraction. The caller still owns its retry guard,
original identity/entitlement, ledger, ACSM/ADEPT fulfillment and exception
handling. Dependency seams are injected for historical service monkeypatches.
No background work, provider network, or profile change is introduced.
"""
from __future__ import annotations

from typing import Any


def acquire_provider_loan(
    service: Any, loan: Any, *, force_refresh: bool,
    time: Any, _TRACE_LOG: Any,
) -> Any:
    provider_started = time.monotonic()
    _TRACE_LOG.info("TRACE download provider begin loan_id=%s provider=%s", loan.id, loan.source_provider)
    acquisition = service.acquire_digital_loan(loan, force_refresh=True) if force_refresh else service.acquire_digital_loan(loan)
    _TRACE_LOG.info(
        "TRACE download provider end loan_id=%s kind=%s file=%s bytes=%s dt=%.3fs",
        loan.id, acquisition.kind, acquisition.path.name if acquisition.path else None,
        acquisition.path.stat().st_size if acquisition.path and acquisition.path.is_file() else 0,
        time.monotonic()-provider_started,
    )
    return acquisition


def index_local_loan_artifact(
    service: Any, loan: Any, identity: Any, final_path: Any, *,
    time: Any, _TRACE_LOG: Any, inspect_artifact: Any,
    ProviderConfigurationError: type[Exception],
) -> tuple[Any, dict]:
    if final_path is None or not final_path.is_file():
        raise ProviderConfigurationError("L'acquisition automatique n'a produit aucun artefact local")
    final_inspection = inspect_artifact(final_path)
    if final_path.suffix.casefold() in {".epub", ".pdf"} and not final_inspection.valid:
        raise ProviderConfigurationError(f"Artefact local invalide: {final_inspection.reason or 'validation'}")
    _TRACE_LOG.info(
        "TRACE download artifact ready loan_id=%s path=%s bytes=%s format=%s protected=%s",
        loan.id, final_path, final_path.stat().st_size, final_inspection.media_format, final_inspection.protected,
    )
    index_started = time.monotonic()
    scan = service.catalog_index_path(
        final_path, title=loan.title, item_id=str(loan.record_id or loan.id),
        identity_id=identity.identity_id, provider_id=loan.source_provider,
    )
    _TRACE_LOG.info("TRACE download catalog index end loan_id=%s status=%s dt=%.3fs", loan.id, scan.get("status"), time.monotonic()-index_started)
    if scan.get("status") != "indexed":
        raise ProviderConfigurationError(f"Indexation locale impossible: {scan.get('reason') or scan.get('status')}")
    return final_inspection, scan
