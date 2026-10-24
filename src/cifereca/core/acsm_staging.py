"""ACSM work-directory and cleanup operations, with explicit local dependencies.

The service owns the success decision, loan serialization, provider/Adobe calls
and retention configuration. This boundary creates staging directories and
removes transient ACSM inputs only when explicitly invoked by that service.
Injected cache, clock, logger and sanitizer preserve facade monkeypatches.
"""
import os
import stat
from pathlib import Path

from .loan_safe_trace import unbound_loan_trace_ref


def _staging_components(identity_id, provider_id, loan_id, component_name):
    parts = ['acquisition-work',
             component_name(identity_id, 'default'),
             component_name(provider_id, 'provider')]
    if loan_id:
        parts.append(component_name(loan_id, 'loan'))
    # Do not rely on an injected sanitizer being correct: dir_fd operations
    # must never accept a traversal component or an absolute path.
    for part in parts:
        if (not isinstance(part, str) or part in ('', '.', '..')
                or '/' in part or '\\' in part):
            raise ValueError('invalid_acquisition_stage_component')
    return parts


def _private_directory(parent_fd, name):
    """Open only a directly owned directory, never an attacker-supplied link."""
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
    except FileExistsError:
        pass
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(name, flags, dir_fd=parent_fd)
    try:
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
            raise PermissionError('untrusted_acquisition_stage_directory')
        # fchmod operates on the verified inode rather than a path, so a
        # rename/symlink swap cannot redirect the permission change.
        if stat.S_IMODE(info.st_mode) != 0o700:
            os.fchmod(fd, 0o700)
        return fd
    except BaseException:
        os.close(fd)
        raise


def acquisition_work_dir(storage_path, identity_id, provider_id, loan_id=None, *, component_name):
    """Create a private 0700 staging tree, independent of umask.

    The storage parent is a trusted application directory. All staging
    components beneath it are opened with O_NOFOLLOW and owned by this user.
    Refuse unsafe pre-existing paths rather than following links or chmodding
    directories belonging to another account.
    """
    parent = Path(storage_path).parent
    parts = _staging_components(identity_id, provider_id, loan_id, component_name)
    parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(parent, flags)
    try:
        if os.fstat(fd).st_uid != os.geteuid():
            raise PermissionError('untrusted_acquisition_storage_parent')
        for part in parts:
            next_fd = _private_directory(fd, part)
            os.close(fd)
            fd = next_fd
    finally:
        os.close(fd)
    return parent.joinpath(*parts)


def cleanup_acsm_after_success(path, *, loan_id, forget, logger):
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
        forget(path)
        logger.info('TRACE download acsm cleanup request_ref=%s action=deleted', unbound_loan_trace_ref(loan_id))
        # Remove only the now-empty directory containing the input.
        parent = path.parent
        try:
            parent.rmdir()
        except OSError:
            pass
    except OSError as exc:
        logger.warning('TRACE download acsm cleanup request_ref=%s action=keep reason=delete_failed', unbound_loan_trace_ref(loan_id))


def cleanup_old_acsm_work(storage_path, *, max_age_seconds=None, config_get, clock, forget, logger):
    root = storage_path.parent / 'acquisition-work'
    if max_age_seconds is None:
        try:
            retention_days = max(1, int(config_get('acquisition.acsm_retention_days', 7) or 7))
        except (TypeError, ValueError):
            retention_days = 7
        max_age_seconds = retention_days * 24 * 3600
    now = clock()
    deleted = kept = 0
    if not root.is_dir():
        return {'deleted': 0, 'kept': 0}
    for path in root.rglob('*.acsm'):
        try:
            age = now - path.stat().st_mtime
            if age >= max_age_seconds:
                path.unlink()
                forget(path)
                deleted += 1
            else:
                kept += 1
        except OSError:
            kept += 1
    if deleted:
        logger.info('TRACE download acsm cleanup-old deleted=%s kept=%s max_age_seconds=%s', deleted, kept, max_age_seconds)
    return {'deleted': deleted, 'kept': kept}
