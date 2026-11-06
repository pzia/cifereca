"""Paris #3118/#1063: explicit Core claim and selected primary account scope.

The optional per-identity primary Paris connection is the authoritative
normal source when configured. Without a saved selection, only ONE enabled
connection is unambiguous. Neither source status nor an ADEPT binding grants
a loan right: the lender still decides after the explicitly claimed click.
"""
from ..models import Account, SearchResult
from .manual_library_borrow import manual_borrow, manual_borrow_available


def _normal_paris_source_current(service, item: SearchResult, identity_id: str) -> bool:
    """Inert, exact-owner current login check; never select another identity.

    Check again inside the provider's Core lease to cover edits occurring
    after button rendering and during descriptive preflight. No profile ADEPT
    enumeration or remote request belongs to this borrowing admission.
    """
    if (type(item) is not SearchResult
            or item.source_provider != "paris"
            or type(item.source_credential) is not str
            or not item.source_credential
            or type(identity_id) is not str or not identity_id):
        return False
    try:
        saved = service.config.identity(identity_id)
        if type(saved) is not dict:
            return False
        selected = saved.get("primary_paris_connection_id")
        if selected is not None and (
                type(selected) is not str or not selected
                or len(selected) > 256 or selected != selected.strip()
                or not selected.isprintable()):
            return False

        rows = service.storage.provider_connections(identity_id=identity_id)
        if type(rows) is not list or len(rows) > 512:
            return False
        enabled = [
            row for row in rows
            if type(row) is dict
            and row.get("identity_id") == identity_id
            and row.get("provider_id") == "paris"
            and row.get("enabled") is True
            and type(row.get("credential_id")) is str
            and row["credential_id"]
        ]
        if selected is None:
            # The legacy implicit selection is safe only with a single
            # eligible Paris source. Never take the first of several.
            return (len(enabled) == 1
                    and enabled[0]["credential_id"] == item.source_credential)
        if selected != item.source_credential:
            return False
        if (len([row for row in enabled
                 if row["credential_id"] == selected]) != 1):
            return False
        configured = service.config.provider_accounts()
        if type(configured) is not list or len(configured) > 2048:
            return False
        matches = [
            row for row in configured
            if type(row) is dict
            and type(row.get("id")) is str and row["id"] == selected
            and type(row.get("provider")) is str
            and row["provider"].casefold() == "paris"
            and row.get("identity_id") == identity_id
            and row.get("enabled", True) is True
        ]
        return len(matches) == 1
    except Exception:
        return False



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
              or not item.source_credential):
            reason = "edition_or_source_unqualified"
        elif not _normal_paris_source_current(service, item, identity_id):
            reason = "primary_paris_connection_mismatch_or_inactive"
        elif manual_borrow_available(
            service, item, provider_id="paris", docbase="DILICOM",
            primary_id="",
        ):
            reason = "local_action_available_provider_unknown"
            possible = True
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
