"""Validate and publish an ADEPT result within the caller's guarded attempt.

The caller retains profile selection, runtime execution, exception handling,
ledger completion and ACSM cleanup. Existing path/content checks and file
mutations preserve their historical order.
"""
from __future__ import annotations


def publish(*, result, adept_destination, acsm_format, loan, repair_mode,
            destination, previous_path, Path, ProviderConfigurationError,
            inspect_artifact, adept_acsm_plan, uuid, _TRACE_LOG):
    artifact = result.get("artifact") if isinstance(result, dict) else None
    artifact_path = str((artifact or {}).get("path") or adept_destination)
    produced_path = Path(artifact_path)
    if produced_path.resolve() != adept_destination.resolve():
        raise ProviderConfigurationError("Chemin d’artefact ADEPT inattendu")
    artifact_drm = str((artifact or {}).get("drm") or "unknown")
    artifact_format = str((artifact or {}).get("format") or acsm_format or "").casefold() or None
    hook_info = ((result or {}).get("hook") or (result or {}).get("post_download")) if isinstance(result, dict) else None
    _TRACE_LOG.info(
        "TRACE download adept artifact loan_id=%s path=%s format=%s drm=%s hook_reported=%s repair_mode=%s",
        loan.id, produced_path, artifact_format, artifact_drm, bool(hook_info), repair_mode,
    )
    if not produced_path.is_file():
        raise ProviderConfigurationError("Kobo-Adept n'a produit aucun artefact")

    actual_before_publish = inspect_artifact(produced_path)
    if not actual_before_publish.valid or not actual_before_publish.media_format:
        raise ProviderConfigurationError(f"Artefact Kobo-Adept invalide: {actual_before_publish.reason or 'format inconnu'}")
    actual_format = actual_before_publish.media_format
    actual_suffix = ".pdf" if actual_format == "pdf" else ".epub"
    corrected = adept_acsm_plan.plan_produced_format_path(
        produced_path, actual_suffix, unique_suffix=lambda: uuid.uuid4().hex,
    )
    if corrected != produced_path:
        produced_path.replace(corrected)
        produced_path = corrected
        _TRACE_LOG.info("TRACE download adept rename-format loan_id=%s format=%s path=%s", loan.id, actual_format, produced_path)
    destination = adept_acsm_plan.plan_publish_format_path(
        destination, actual_suffix, repair_mode=repair_mode,
        unique_suffix=lambda: uuid.uuid4().hex,
    )
    inspection = inspect_artifact(produced_path, actual_format)
    if not inspection.valid:
        raise ProviderConfigurationError(f"Artefact final invalide: {inspection.reason or 'validation'}")
    artifact_format = actual_format
    artifact_protected = inspection.protected
    if artifact_protected is True:
        raise ProviderConfigurationError("Artefact final encore protégé après Kobo-Adept")

    if repair_mode:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and destination.resolve() != produced_path.resolve():
            destination.unlink()
        produced_path.replace(destination)
        final_path = destination
        if previous_path is not None and previous_path.exists() and previous_path.resolve() != destination.resolve():
            previous_path.unlink(missing_ok=True)
        _TRACE_LOG.info("TRACE download adept repair-replace loan_id=%s path=%s", loan.id, destination)
    else:
        if produced_path.resolve() != destination.resolve():
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.unlink(missing_ok=True)
            produced_path.replace(destination)
        final_path = destination
    return final_path, artifact_drm, artifact_format, artifact_protected, hook_info
