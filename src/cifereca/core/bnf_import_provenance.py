"""A local import's observed loan is a display link, never a receipt/right."""
from pathlib import Path

# Public compatibility names: the display join is provider-neutral.
from .observed_loan_scope import FIELDS, observed_loan, scope_key


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
    """BnF observed-loan hints are read-only, never ownership or receipt."""
    from .local_import_display_links import observed_import_display_links
    return observed_import_display_links(
        storage, placements, local_keys,
        provenance_field='bnf_observed_loan',
        placement_provider_id='bnf-pret',
        validate_scope=scope_key,
    )
