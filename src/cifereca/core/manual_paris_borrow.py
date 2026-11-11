"""Paris #3118/#1063: explicit Core claim and selected primary account scope.

The optional per-identity primary Paris connection is the authoritative
normal source when configured. Without a saved selection, only ONE enabled
connection is unambiguous. Neither source status nor an ADEPT binding grants
a loan right: the lender still decides after the explicitly claimed click.
"""
from ..models import Account, SearchResult
from .manual_library_borrow import (manual_borrow, manual_borrow_available,
                                    manual_borrow_diagnostic)
from .primary_source_binding import configured_primary_source_current


def _normal_paris_source_current(service, item: SearchResult, identity_id: str) -> bool:
    return configured_primary_source_current(
        service, item, identity_id, provider_id="paris",
        selection_key="primary_paris_connection_id",
    )



def manual_paris_borrow_diagnostic(service, item: SearchResult) -> dict[str, object]:
    """Read-only Paris projection; never an eligibility or download grant."""
    result = manual_borrow_diagnostic(
        service, item, provider_id="paris", docbase="DILICOM",
        primary_id="", source_current=_normal_paris_source_current,
        available=manual_borrow_available, result_type=SearchResult,
    )
    if result["reason_code"] == "local_primary_source_binding_mismatch":
        result["reason_code"] = "primary_paris_connection_mismatch_or_inactive"
    return result


def manual_paris_borrow_available(service, item: SearchResult) -> bool:
    # UI and the read-only diagnostic consume exactly the same admission.
    return manual_paris_borrow_diagnostic(service, item)["local_action_possible"] is True


def manual_paris_borrow(
    service, item: SearchResult, *, expected_identity_id: str,
    confirmed: bool = False, expected_context_epoch: int | None = None,
    expected_account: Account | None = None,
) -> dict[str, object]:
    # Keep the historical explicit-click refusal reason (including
    # confirmed=False) owned by the generic claim path.
    def current() -> bool:
        return _normal_paris_source_current(service, item, expected_identity_id)

    return manual_borrow(
        service, item, provider_id="paris", docbase="DILICOM", primary_id="",
        expected_identity_id=expected_identity_id, confirmed=confirmed,
        expected_context_epoch=expected_context_epoch,
        expected_account=expected_account,
        additional_scope_current=current,
    )


__all__ = ["manual_paris_borrow", "manual_paris_borrow_available",
           "manual_paris_borrow_diagnostic"]
