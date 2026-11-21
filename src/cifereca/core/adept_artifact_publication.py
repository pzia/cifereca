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
    """Pin both directories; make the final name durable before stage retirement.

    Share the direct-loan physical primitives, not its naming policy. This
    is still a physical bridge, not the FS/SQLite commit or replay protocol.
    """
    import os
    import secrets
    import stat
    from .library_inventory import _open_root
    from .loan_direct_publication import (
        _create_directory, _current_directory, _info, _owned, _remove_own,
        _source_current, _source_stamp,
    )

    if (not all(path.is_absolute() and ".." not in path.parts and path.name
                for path in (produced_path, destination))
            or any(path.is_symlink() for file in (produced_path, destination)
                   for path in (file, *file.parents))):
        raise ProviderConfigurationError("Chemin d’artefact ADEPT inattendu")

    stage_fd = dest_fd = None
    temporary = made = linked = None
    try:
        stage_fd = _open_root(produced_path.parent)
        before = _info(stage_fd, produced_path.name)
        # ADEPT output is a private stage, not an alias of another artifact.
        # Preserve the direct-loan no-clobber ownership contract (#5225).
        if (not stat.S_ISREG(before.st_mode) or before.st_size <= 0
                or before.st_nlink != 1):
            raise ProviderConfigurationError("Artefact final invalide")

        def stage_current():
            return (_current_directory(produced_path.parent, stage_fd)
                    and _source_current(stage_fd, produced_path.name, before))

        if produced_path == destination:
            # A stage cannot simultaneously be the final library placement.
            # Accepting it would bypass the exclusive link/fsync protocol and
            # silently make a provisional ADEPT name a published artifact.
            raise ProviderConfigurationError("Chemin d’artefact ADEPT inattendu")
        dest_fd = _create_directory(destination.parent)
        if not _current_directory(destination.parent, dest_fd):
            raise ProviderConfigurationError("Chemin d’artefact ADEPT inattendu")

        source_fd = os.open(produced_path.name, os.O_RDONLY | os.O_NOFOLLOW
                            | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=stage_fd)
        with os.fdopen(source_fd, "rb") as source:
            now = os.fstat(source.fileno())
            if (not stat.S_ISREG(now.st_mode) or now.st_nlink != 1
                    or _source_stamp(now) != _source_stamp(before)):
                raise ProviderConfigurationError("Artefact final modifié")
            temporary = ".adept-publish-" + secrets.token_hex(16)
            out_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=dest_fd)
            with os.fdopen(out_fd, "wb") as out:
                made = os.fstat(out.fileno())
                try:
                    while True:
                        part = source.read(1024 * 1024)
                        if not part:
                            break
                        out.write(part)
                    out.flush()
                    os.fsync(out.fileno())
                finally:
                    made = os.fstat(out.fileno())
            if (_source_stamp(os.fstat(source.fileno())) != _source_stamp(before)
                    or made.st_size != before.st_size or not stage_current()):
                raise ProviderConfigurationError("Artefact final modifié")

        initial = destination
        for _attempt in range(32):
            if (not _current_directory(initial.parent, dest_fd)
                    or not _owned(dest_fd, temporary, made) or not stage_current()):
                raise ProviderConfigurationError("Artefact final modifié")
            try:
                os.link(temporary, destination.name, src_dir_fd=dest_fd,
                        dst_dir_fd=dest_fd, follow_symlinks=False)
            except FileExistsError:
                destination = initial.with_name(
                    initial.stem + ".reacquired-" + uuid.uuid4().hex + initial.suffix
                )
                continue
            linked = destination.name

            def names_current():
                return (_current_directory(initial.parent, dest_fd)
                        and _owned(dest_fd, linked, made) and stage_current())

            if not names_current():
                raise ProviderConfigurationError("Artefact final modifié")
            os.fsync(dest_fd)
            if not names_current():
                raise ProviderConfigurationError("Artefact final modifié")
            # The final fsync has completed, but retirement still requires
            # the private source's ctime and single-link ownership to match.
            # Do not use the destination's weaker hardlink stamp to authorize
            # a staging unlink after a concurrent source alias appears.
            if not stage_current():
                raise ProviderConfigurationError("Artefact final modifié")
            _remove_own(stage_fd, produced_path.name, before)
            linked = None
            return destination
        raise ProviderConfigurationError("Destination ADEPT déjà occupée")
    finally:
        if dest_fd is not None:
            if linked is not None and made is not None:
                _remove_own(dest_fd, linked, made)
                try:
                    os.fsync(dest_fd)
                except OSError:
                    pass
            if temporary is not None and made is not None:
                _remove_own(dest_fd, temporary, made)
            os.close(dest_fd)
        if stage_fd is not None:
            os.close(stage_fd)


def publish(*, result, adept_destination, acsm_format, loan, repair_mode,
            destination, previous_path, Path, ProviderConfigurationError,
            inspect_artifact, adept_acsm_plan, uuid, _TRACE_LOG, identity_id=None):
    trace_ref = loan_trace_ref(loan, identity_id if identity_id is not None else getattr(loan, "identity_id", None))
    artifact = result.get("artifact") if isinstance(result, dict) else None
    # The delegated runtime is an untrusted boundary. A non-mapping artifact
    # cannot be interpreted as an absent artifact or raise into the loan worker.
    if artifact is not None and type(artifact) is not dict:
        raise ProviderConfigurationError("Résultat ADEPT invalide")
    artifact_path = str((artifact or {}).get("path") or adept_destination)
    produced_path = Path(artifact_path)
    # #5225: never interpret a symlinked file or a swapped staging/profile
    # directory as ADEPT's newly produced bytes. Equality after resolve()
    # alone would accept a symlink to an unrelated existing artifact.
    if any(path.is_symlink() for file in (produced_path, adept_destination, destination)
           for path in (file, *file.parents)):
        raise ProviderConfigurationError("Chemin d’artefact ADEPT inattendu")
    if produced_path != adept_destination:
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
    # Inspect the original stage by contents, not its provisional suffix.
    # Never rename it to a guessed intermediate path: even an absent name
    # can be claimed by an independent writer before replace(). The exclusive
    # publication below chooses the final suffix directly.
    if produced_path.suffix.casefold() != actual_suffix:
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
