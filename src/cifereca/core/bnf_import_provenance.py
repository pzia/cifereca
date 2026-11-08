"""A local import's observed loan is a display link, never a receipt/right."""

# Public compatibility names: the display join is provider-neutral.
from .observed_loan_scope import FIELDS, observed_loan, scope_key


def remember_import(service, result, scope):
    """Bind only exact bytes to a display-only BnF loan observation.

    Core storage retains the authority to reject a foreign borrower,
    acquisition, artifact or placement. Never manufacture a provider right.
    """
    if not scope_key(scope):
        return
    from .manual_import_display_admission import verified_manual_import_candidate
    candidate = verified_manual_import_candidate(service, result)
    if candidate is None:
        return
    acquisition_id, path, artifact_id = candidate
    service.storage.acquisition_bind_bnf_display_loan(
        acquisition_id, path=str(path), artifact_id=artifact_id, scope=scope)


def display_links(storage, placements, local_keys):
    """BnF observed-loan hints are read-only, never ownership or receipt."""
    from .local_import_display_links import observed_import_display_links
    return observed_import_display_links(
        storage, placements, local_keys,
        provenance_field='bnf_observed_loan',
        placement_provider_id='bnf-pret',
        validate_scope=scope_key,
    )
