"""Validate and publish an ADEPT result within the caller's guarded attempt.

The caller retains profile selection, runtime execution, exception handling,
ledger completion and ACSM cleanup. Existing path/content checks and file
mutations preserve their historical order.
"""
from __future__ import annotations

from .loan_safe_trace import loan_trace_ref


def publish(*, result, adept_destination, acsm_format, loan, repair_mode,
            destination, previous_path, Path, ProviderConfigurationError,
            inspect_artifact, adept_acsm_plan, uuid, _TRACE_LOG, identity_id=None):
    trace_ref = loan_trace_ref(loan, identity_id if identity_id is not None else getattr(loan, "identity_id", None))
    artifact = result.get("artifact") if isinstance(result, dict) else None
    artifact_path = str((artifact or {}).get("path") or adept_destination)
    produced_path = Path(artifact_path)
    if produced_path.resolve() != adept_destination.resolve():
        raise ProviderConfigurationError("Chemin d’artefact ADEPT inattendu")
    artifact_drm = str((artifact or {}).get("drm") or "unknown")
    artifact_format = str((artifact or {}).get("format") or acsm_format or "").casefold() or None
    hook_info = ((result or {}).get("hook") or (result or {}).get("post_download")) if isinstance(result, dict) else None
    _TRACE_LOG.info(
        "TRACE download adept artifact loan_ref=%s hook_reported=%s repair_mode=%s",
        trace_ref, bool(hook_info), bool(repair_mode),
    )
    if not produced_path.is_file():
        raise ProviderConfigurationError("Kobo-Adept n'a produit aucun artefact")

    actual_before_publish = inspect_artifact(produced_path)
    if not actual_before_publish.valid or not actual_before_publish.media_format:
        # The inspection reason can contain untrusted file/runtime details.
        # Never expose it in the public ADEPT failure or durable ledger.
        raise ProviderConfigurationError("Artefact Kobo-Adept invalide")
    actual_format = actual_before_publish.media_format
    actual_suffix = ".pdf" if actual_format == "pdf" else ".epub"
    corrected = adept_acsm_plan.plan_produced_format_path(
        produced_path, actual_suffix, unique_suffix=lambda: uuid.uuid4().hex,
    )
    if corrected != produced_path:
        produced_path.replace(corrected)
        produced_path = corrected
        _TRACE_LOG.info("TRACE download adept rename-format loan_ref=%s", trace_ref)
    destination = adept_acsm_plan.plan_publish_format_path(
        destination, actual_suffix, repair_mode=repair_mode,
        unique_suffix=lambda: uuid.uuid4().hex,
    )
    inspection = inspect_artifact(produced_path, actual_format)
    if not inspection.valid:
        raise ProviderConfigurationError("Artefact final invalide")
    artifact_format = actual_format
    artifact_protected = inspection.protected
    if artifact_protected is True:
        raise ProviderConfigurationError("Artefact final encore protégé après Kobo-Adept")

    if repair_mode:
        destination.parent.mkdir(parents=True, exist_ok=True)
        # os.replace is atomic on the destination filesystem. Do NOT unlink
        # the existing valid publication first: if the rename fails (quota,
        # permissions, power loss), the previously indexed artifact must
        # remain readable for safe replay/rollback.
        produced_path.replace(destination)
        final_path = destination
        # IMPORTANT: publication is NOT the Core/SQLite commit. Keep the
        # old version until catalog indexing and acquisition_put(completed)
        # succeed. This prevents an index/ledger error from destroying the
        # last recorded artifact. The caller invokes cleanup_after_commit().
        _TRACE_LOG.info("TRACE download adept repair-replace loan_ref=%s", trace_ref)
    else:
        if produced_path.resolve() != destination.resolve():
            destination.parent.mkdir(parents=True, exist_ok=True)
            # Replacing the existing file is already atomic. Unlink-first
            # creates an avoidable window where an earlier valid loan is lost
            # if publication fails before the SQLite receipt is committed.
            produced_path.replace(destination)
        final_path = destination
    return final_path, artifact_drm, artifact_format, artifact_protected, hook_info


def cleanup_after_commit(*, previous_path, final_path, repair_mode: bool,
                         Path, _TRACE_LOG, loan, identity_id=None) -> bool:
    """Best-effort, post-ledger cleanup of a *distinct* prior repair artifact.

    A cleanup error must never convert a committed acquisition into a failed
    loan or a second provider request. Never follow a symlink or include file
    paths, title, loan identifiers, or exceptions in diagnostics.
    """
    if (repair_mode is not True
            or type(previous_path) is not Path
            or type(final_path) is not Path
            or previous_path.is_symlink()
            or final_path.is_symlink()):
        return False
    try:
        if (not previous_path.is_file()
                or not final_path.is_file()
                or previous_path.resolve() == final_path.resolve()):
            return False
        previous_path.unlink()
    except OSError:
        _TRACE_LOG.warning(
            "TRACE download adept prior-cleanup loan_ref=%s status=retained",
            loan_trace_ref(loan, identity_id if identity_id is not None
                           else getattr(loan, "identity_id", None)),
        )
        return False
    _TRACE_LOG.info(
        "TRACE download adept prior-cleanup loan_ref=%s status=removed",
        loan_trace_ref(loan, identity_id if identity_id is not None
                       else getattr(loan, "identity_id", None)),
    )
    return True
