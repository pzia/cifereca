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
            or type(identity_id) is not str or not identity_id
            or (type(getattr(item, "metadata", None)) is dict
                and item.metadata.get("catalogue_access") == "shared_read_only")):
        # A shared catalogue projection is never an action source, even
        # when its owner later becomes the selected identity (A -> B).
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


def _pending_paris_borrow_scope(service, item: SearchResult):
    """Return one private exact claim, never an operation reference to the UI.

    This is a read-only lookup of a previously *consumed* borrow attempt.
    No fresh claim, GET, POST, provider permission or source remapping.
    """
    from .borrow_action_scope import borrow_claim_key
    from .loan_action_scope import _inert_session_accounts, _inert_session_state

    identity = getattr(getattr(service, "current_identity", None), "identity_id", None)
    if (type(item) is not SearchResult
            or type(identity) is not str or not identity
            or item.docbase != "DILICOM" or item.medium != "digital"
            or not _normal_paris_source_current(service, item, identity)):
        return None
    try:
        session = service._session_for_source("paris", item.source_credential)
        registered = _inert_session_accounts(session)
        state = _inert_session_state(session)
        if (registered is None or state is None
                or state.get("connected") is not True
                or state.get("_closed") is True):
            return None
        accounts = [
            row for row in service.accounts
            if (type(row) is Account and row.is_primary is True
                and row.unique_id == "" and row.identity_id == identity
                and row.provider_id == "paris"
                and row.credential_id == item.source_credential
                and sum(member is row for member in registered) == 1)
        ]
        if len(accounts) != 1:
            return None
        account = accounts[0]
        key = borrow_claim_key(account, item)
        if key is None:
            return None
        checkpoint = service.storage.loan_borrow_state(key)
        if (type(checkpoint) is not dict
                or checkpoint.get("status") != "verification_unavailable"):
            return None
        operation_id = checkpoint.get("operation_id")
        if (type(operation_id) is not str
                or not 1 <= len(operation_id) <= 128
                or any(ord(c) < 33 or ord(c) > 126 for c in operation_id)):
            return None
        return account, operation_id
    except Exception:
        return None


def paris_borrow_followup_available(service, item: SearchResult) -> bool:
    """Inert local UI admission for one pending Paris loan check after restart."""
    return _pending_paris_borrow_scope(service, item) is not None


def verify_paris_borrow_followup(service, item: SearchResult) -> dict[str, object]:
    """Re-read a prior uncertain claim, never initiate or clear a loan request.

    The one-use operation ID is resolved internally from the exact durable
    checkpoint. The public result contains a closed status/reason only.
    """
    def blocked():
        return {
            "status": "blocked", "reason_code": "borrow_verification_claim_mismatch",
            "retry_borrow": False, "mutated": False,
        }

    pinned = _pending_paris_borrow_scope(service, item)
    if pinned is None:
        return blocked()
    account, operation_id = pinned
    result = service.verify_borrow_without_submission(
        item, expected_identity_id=account.identity_id,
        expected_account=account, expected_operation_id=operation_id,
        expected_provider_id="paris", expected_docbase="DILICOM",
    )
    # An account can be disabled or replaced during provider I/O, even when
    # the underlying legacy session object remains registered. Never publish
    # a positive result after that change.
    if (_pending_paris_borrow_scope(service, item) != pinned
            or not _normal_paris_source_current(service, item, account.identity_id)):
        return blocked()
    if (type(result) is not dict
            or result.get("status") not in {
                "blocked", "unavailable", "observed_current_loan",
            }
            or result.get("retry_borrow") is not False
            or result.get("mutated") is not False):
        return blocked()
    return {
        "status": result["status"],
        "reason_code": (
            result.get("reason_code")
            if type(result.get("reason_code")) is str
            and result["reason_code"] in {
                "borrow_verification_scope_invalid",
                "borrow_verification_claim_mismatch",
                "borrow_verification_source_changed",
                "borrow_verification_observation_invalid",
                "borrow_verification_observation_partial",
                "borrow_verification_loan_unconfirmed",
                "borrow_verification_current_loan_observed",
                "borrow_verification_observation_unavailable",
            }
            else "borrow_verification_observation_unavailable"
        ),
        "retry_borrow": False,
        "mutated": False,
    }

__all__ = ["manual_paris_borrow", "manual_paris_borrow_available",
           "manual_paris_borrow_diagnostic", "paris_borrow_followup_available",
           "verify_paris_borrow_followup"]
