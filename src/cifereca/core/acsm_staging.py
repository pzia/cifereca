"""ACSM work-directory and cleanup operations, with explicit local dependencies.

The service owns the success decision, loan serialization, provider/Adobe calls
and retention configuration. This boundary creates staging directories and
removes transient ACSM inputs only when explicitly invoked by that service.
Injected cache, clock, logger and sanitizer preserve facade monkeypatches.
"""
from .loan_safe_trace import unbound_loan_trace_ref


def acquisition_work_dir(storage_path, identity_id, provider_id, loan_id=None, *, component_name):
    """Return a mutable staging directory outside the user library tree."""
    base = (storage_path.parent / 'acquisition-work'
            / component_name(identity_id, 'default')
            / component_name(provider_id, 'provider'))
    if loan_id:
        base = base / component_name(loan_id, 'loan')
    base.mkdir(parents=True, exist_ok=True)
    return base


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
