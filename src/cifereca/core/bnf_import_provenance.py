"""A local import's observed loan is a display link, never a receipt/right."""
from pathlib import Path

FIELDS = ('identity_id', 'credential_id', 'account_id', 'loan_id', 'item_id')


def observed_loan(loan):
    return dict(zip(FIELDS, (loan.identity_id, loan.source_credential,
                            loan.source_account_id, loan.id, loan.record_id)))


def scope_key(value):
    if (type(value) is not dict or set(value) != set(FIELDS)
            or any(type(value.get(k)) is not str or not value[k] for k in FIELDS)):
        return None
    return tuple(value[k] for k in FIELDS)


def remember_import(service, result, scope):
    """Bind only actual, unchanged bytes in an indexed BnF placement.

    Older manual placements stay at their original paths and acquire no new
    source attribution. A repeated token cannot overwrite its first borrower.
    """
    if result.get('status') not in {'complete', 'unchanged'} or not scope_key(scope):
        return
    acquisition_id = result.get('acquisition_id')
    if not acquisition_id:
        return
    row = service.storage.acquisition_get(acquisition_id)
    if not row:
        return
    from ..artifact_inspection import inspect_artifact
    from ..library_files import sha256_file
    path = Path(str(result.get('path') or ''))
    if (path.is_symlink() or not path.is_file() or not inspect_artifact(path).valid
            or row.get('artifact_id') != 'sha256:' + sha256_file(path)):
        return
    service.storage.acquisition_bind_bnf_display_loan(
        acquisition_id, path=str(path), artifact_id=row['artifact_id'], scope=scope)


def display_links(storage, placements, local_keys):
    """Only exact, unambiguous SQLite links; no filesystem/provider work."""
    candidates = {}
    by_path = {p.get('path'): p for p in placements}
    for row in storage.acquisitions(provider_id='manual', status='complete'):
        meta = row.get('metadata') or {}
        scope = meta.get('bnf_observed_loan')
        key = scope_key(scope)
        if (not key or row.get('identity_id') != key[0] or row.get('entitlement_id')
                or row.get('strategy') != 'manual_acsm'):
            continue
        placement = by_path.get(meta.get('path'), {})
        artifact = row.get('artifact_id')
        local_key = local_keys.get((placement.get('placement_id'), artifact))
        if (local_key and placement.get('identity_id') == key[0]
                and placement.get('provider_id') == 'bnf-pret'
                and placement.get('artifact_id') == artifact):
            candidates.setdefault(key, set()).add(local_key)
    return {scope: next(iter(keys)) for scope, keys in candidates.items() if len(keys) == 1}
