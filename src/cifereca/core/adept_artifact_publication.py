"""Validate and publish an ADEPT result within the caller's guarded attempt.

The caller retains profile selection, runtime execution, exception handling,
ledger completion and ACSM cleanup. Existing path/content checks and file
mutations preserve their historical order.
"""
from __future__ import annotations

from .loan_safe_trace import loan_trace_ref


def _publish_new_adept_without_clobber(
    produced_path, destination, *, uuid, ProviderConfigurationError,
):
    """No-clobber ADEPT publication even across separate source mounts.

    The Core-owned staging directory may be on another filesystem. Never
    hardlink the staged file directly into Acquisitions (EXDEV); copy and
    fsync first into a private temporary file ON the destination filesystem,
    then atomically publish using an exclusive hardlink. Source cleanup is
    best-effort after physical publication, not an extra provider request.
    """
    import os
    import stat
    import tempfile

    if produced_path.resolve() == destination.resolve():
        return destination
    if (produced_path.is_symlink() or produced_path.parent.is_symlink()
            or destination.is_symlink() or destination.parent.is_symlink()):
        raise ProviderConfigurationError("Chemin d’artefact ADEPT inattendu")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.parent.is_symlink():
        raise ProviderConfigurationError("Chemin d’artefact ADEPT inattendu")

    before = produced_path.stat(follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode) or before.st_size <= 0:
        raise ProviderConfigurationError("Artefact final invalide")

    def same_stage(stat_result):
        return (stat_result is not None
                and stat.S_ISREG(stat_result.st_mode)
                and (stat_result.st_dev, stat_result.st_ino,
                     stat_result.st_size, stat_result.st_mtime_ns)
                    == (before.st_dev, before.st_ino,
                        before.st_size, before.st_mtime_ns))

    temporary = None
    try:
        # No symlink following, and pin the exact previously inspected inode.
        source_fd = os.open(
            produced_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        with os.fdopen(source_fd, "rb") as source:
            if not same_stage(os.fstat(source.fileno())):
                raise ProviderConfigurationError("Artefact final modifié")
            out_fd, temp_name = tempfile.mkstemp(
                prefix=".adept-publish-", dir=destination.parent,
            )
            temporary = type(destination)(temp_name)
            with os.fdopen(out_fd, "wb") as out:
                while True:
                    part = source.read(1024 * 1024)
                    if not part:
                        break
                    out.write(part)
                out.flush()
                os.fsync(out.fileno())
            if not same_stage(os.fstat(source.fileno())):
                raise ProviderConfigurationError("Artefact final modifié")

        if temporary.stat(follow_symlinks=False).st_size != before.st_size:
            raise ProviderConfigurationError("Artefact final modifié")
        if (produced_path.is_symlink()
                or not same_stage(produced_path.stat(follow_symlinks=False))):
            raise ProviderConfigurationError("Artefact final modifié")

        initial = destination
        temp_stat = temporary.stat(follow_symlinks=False)
        for _attempt in range(32):
            try:
                os.link(temporary, destination, follow_symlinks=False)
            except FileExistsError:
                destination = initial.with_name(
                    initial.stem + ".reacquired-" + uuid.uuid4().hex
                    + initial.suffix
                )
                continue

            # The same-filesystem hardlink must refer to our private
            # temporary inode. Never unlink a path already replaced by a
            # different concurrent operation.
            published = destination.stat(follow_symlinks=False)
            if ((published.st_dev, published.st_ino, published.st_size)
                    != (temp_stat.st_dev, temp_stat.st_ino,
                        temp_stat.st_size)):
                raise ProviderConfigurationError("Artefact final modifié")
            try:
                current = produced_path.stat(follow_symlinks=False)
            except OSError:
                current = None
            if not same_stage(current):
                # Source path was rebound even after the validated copy:
                # undo ONLY our own newly linked destination.
                published_now = destination.stat(follow_symlinks=False)
                if ((published_now.st_dev, published_now.st_ino)
                        == (temp_stat.st_dev, temp_stat.st_ino)):
                    destination.unlink()
                raise ProviderConfigurationError("Artefact final modifié")
            try:
                produced_path.unlink()
            except OSError:
                pass
            return destination
        raise ProviderConfigurationError("Destination ADEPT déjà occupée")
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

def publish(*, result, adept_destination, acsm_format, loan, repair_mode,
            destination, previous_path, Path, ProviderConfigurationError,
            inspect_artifact, adept_acsm_plan, uuid, _TRACE_LOG, identity_id=None):
    trace_ref = loan_trace_ref(loan, identity_id if identity_id is not None else getattr(loan, "identity_id", None))
    artifact = result.get("artifact") if isinstance(result, dict) else None
    artifact_path = str((artifact or {}).get("path") or adept_destination)
    produced_path = Path(artifact_path)
    # #5225: never interpret a symlinked file or a swapped staging/profile
    # directory as ADEPT's newly produced bytes. Equality after resolve()
    # alone would accept a symlink to an unrelated existing artifact.
    if (produced_path.is_symlink() or adept_destination.is_symlink()
            or produced_path.parent.is_symlink()
            or destination.is_symlink() or destination.parent.is_symlink()):
        raise ProviderConfigurationError("Chemin d’artefact ADEPT inattendu")
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

    # Even a legacy repair is a publication of new bytes. A filename
    # collision is not evidence of ownership; replacing it before SQLite
    # has committed can destroy the only recorded copy (or another loan).
    final_path = _publish_new_adept_without_clobber(
        produced_path, destination, uuid=uuid,
        ProviderConfigurationError=ProviderConfigurationError,
    )
    if repair_mode:
        _TRACE_LOG.info("TRACE download adept repair-preserve loan_ref=%s", trace_ref)
    return final_path, artifact_drm, artifact_format, artifact_protected, hook_info


def cleanup_after_commit(*, previous_path, final_path, repair_mode: bool,
                         Path, _TRACE_LOG, loan, identity_id=None) -> bool:
    """Retain the prior repair artifact until dependency-qualified maintenance.

    A completed ledger receipt for the replacement is not proof that no
    other placement, historical receipt or independent provenance refers to
    the old file. Keep this compatibility hook read-only until a supervised
    plan can establish retirement safety. No second GET is authorized.
    """
    if repair_mode is True and isinstance(previous_path, Path):
        _TRACE_LOG.info(
            "TRACE download adept prior-cleanup loan_ref=%s status=retained",
            loan_trace_ref(loan, identity_id if identity_id is not None
                           else getattr(loan, "identity_id", None)),
        )
    return False
