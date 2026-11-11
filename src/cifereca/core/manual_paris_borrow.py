"""Paris #3118/#1063: explicit Core claim and selected primary account scope.

The optional per-identity primary Paris connection is the authoritative
normal source when configured. Without a saved selection, only ONE enabled
connection is unambiguous. Neither source status nor an ADEPT binding grants
a loan right: the lender still decides after the explicitly claimed click.
"""
from ..models import Account, SearchResult
from .manual_library_borrow import manual_borrow, manual_borrow_available\nfrom .primary_source_binding import configured_primary_source_current


def _normal_paris_source_current(service, item: SearchResult, identity_id: str) -> bool:
    return configured_primary_source_current(
        service, item, identity_id, provider_id="paris",
        selection_key="primary_paris_connection_id",
    )



def manual_paris_borrow_diagnostic(service, item: SearchResult) -> dict[str, object]:
    """Read-only local admission reason; NOT a lender eligibility oracle.

    In particular, a valid local action never proves personal quota or
    account-specific permission at the Paris supplier. No provider I/O.
    Closed reason codes only: no credentials, notice text or identifiers.
    """
    reason = "local_admission_unavailable"
    possible = False
    try:
        identity_id = getattr(
            getattr(service, "current_identity", None), "identity_id", None
        )
        if type(identity_id) is not str or not identity_id:
            reason = "identity_not_selected"
        elif (type(item) is not SearchResult
              or item.source_provider != "paris"
              or item.docbase != "DILICOM"
              or item.medium != "digital"
              or type(item.source_credential) is not str
              or not item.source_credential
              or (type(getattr(item, "metadata", None)) is dict
                  and item.metadata.get("catalogue_access") == "shared_read_only")):
            reason = "edition_or_source_unqualified"
        elif not _normal_paris_source_current(service, item, identity_id):
            reason = "primary_paris_connection_mismatch_or_inactive"
        else:
            admission = {}
            possible = manual_borrow_available(
                service, item, provider_id="paris", docbase="DILICOM",
                primary_id="", diagnostic=admission,
            ) is True
            reason = admission.get("reason_code", "local_admission_unavailable")
            # The source must never inject arbitrary strings into diagnostics.
            if reason not in {
                "local_action_available_provider_unknown",
                "local_edition_scope_invalid",
                "local_provider_or_identity_unavailable",
                "local_session_unavailable",
                "local_primary_account_unverified",
                "local_claim_scope_invalid",
                "local_previous_attempt",
                "local_scope_changed",
                "local_borrow_capability_unavailable",
                "local_borrow_executor_unavailable",
                "local_admission_unavailable",
            }:
                possible = False
                reason = "local_admission_unavailable"
    except Exception:
        # Unexpected access failure must never manufacture eligibility.
        reason = "local_admission_unavailable"
    return {
        "local_action_possible": possible,
        "reason_code": reason,
        # Public Search availability is not account-specific proof.
        "provider_eligibility": "unknown",
    }


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
