"""Versioned, read-only admission bridge for provider-declared manual borrowing.

This policy is an inert installed-plugin declaration, NEVER borrower permission.
The existing generic Core manual-borrow implementation alone owns consent,
live lease, durable CAS claim, provider dispatch and independent verification.
No fallback to the legacy session.borrow() is permitted by this bridge.
"""
from __future__ import annotations

from ..models import SearchResult
from ..providers.base import ManualBorrowPolicy, ProviderDescriptor


def resolved_manual_borrow_policy(service, item: object, *, reader_scope=None) -> ManualBorrowPolicy | None:
    """Accept only an exact, installed and enabled source's typed contract."""
    if (type(item) is not SearchResult
            or type(item.source_provider) is not str or not item.source_provider
            or type(item.source_credential) is not str or not item.source_credential
            or type(item.docbase) is not str or item.medium != "digital"):
        return None
    try:
        plugin_id = service._provider_plugin_ids[item.source_provider]
        status = service._plugin_manager.status(plugin_id)
        if (status.enabled is not True or status.state != "ready"
                or status.descriptor.id != plugin_id
                or status.descriptor.type != "provider.library"
                or "acquisition.borrow" not in status.descriptor.capabilities):
            return None
        if reader_scope is None:
            session = service._session_for_source(
                item.source_provider, item.source_credential,
            )
        else:
            from .search_orchestration import SearchReadScope
            from .provider_sessions import _session_for_source
            from .loan_action_scope import _inert_session_state
            if (type(reader_scope) is not SearchReadScope
                    or not reader_scope.current(service)
                    or service._provider_enabled_for_identity(
                        item.source_provider, reader_scope.identity_id,
                    ) is not True):
                return None
            session = _session_for_source(
                service, item.source_provider, item.source_credential,
                identity_id=reader_scope.identity_id,
            )
            if _inert_session_state(session) is None:
                return None
        descriptor = session.descriptor
        if (type(descriptor) is not ProviderDescriptor
                or descriptor.id != item.source_provider
                or descriptor.state != "ready"
                or descriptor.capabilities.borrow is not True
                or session.provider_id != item.source_provider
                or session.credential_id != item.source_credential
                or session.connected is not True):
            return None
        policy = descriptor.manual_borrow_policy
        if (type(policy) is not ManualBorrowPolicy
                or policy.schema != "cifereca.manual-borrow/1"
                or item.docbase != policy.docbase):
            return None
        if reader_scope is not None and not reader_scope.current(service):
            return None
        return policy
    except Exception:
        return None


def _selected_primary_source_current(service, item, identity_id, policy):
    if policy.source_selection_key is None:
        return True
    from .primary_source_binding import configured_primary_source_current
    return configured_primary_source_current(
        service, item, identity_id, provider_id=item.source_provider,
        selection_key=policy.source_selection_key,
    )


def manual_borrow_action_diagnostic(
    service, item: SearchResult, *, identity_id: str | None = None,
) -> dict[str, object]:
    """Inert, provider-neutral scoped admission for new or spent requests.

    This result never attests subscription, quota, lender permission or
    completed borrowing. The resolved installed policy, not the public
    catalogue, defines the account and edition scope.
    """
    from .manual_library_borrow import manual_borrow_diagnostic
    policy = resolved_manual_borrow_policy(service, item)
    if policy is None:
        return {
            "local_action_possible": False,
            "reason_code": "edition_or_source_unqualified",
            "provider_eligibility": "unknown",
        }
    try:
        return manual_borrow_diagnostic(
            service, item, provider_id=item.source_provider,
            docbase=policy.docbase, primary_id=policy.primary_borrower_id,
            identity_id=identity_id,
            source_current=lambda service_, item_, identity: (
                resolved_manual_borrow_policy(service_, item_) == policy
                and _selected_primary_source_current(
                    service_, item_, identity, policy,
                ) is True
            ),
        )
    except Exception:
        # Never publish a supplier error, account identifier or private URL.
        return {
            "local_action_possible": False,
            "reason_code": "local_admission_unavailable",
            "provider_eligibility": "unknown",
        }


def manual_borrow_action_available(
    service, item: SearchResult, *, identity_id: str | None = None,
) -> bool:
    """Inert visibility; supplier quota/offer/permission remain unknown."""
    from .manual_library_borrow import (
        manual_borrow_available, manual_borrow_diagnostic,
    )
    policy = resolved_manual_borrow_policy(service, item)
    if policy is None:
        return False
    if policy.source_selection_key is None:
        return manual_borrow_available(
            service, item, provider_id=item.source_provider,
            docbase=policy.docbase, primary_id=policy.primary_borrower_id,
            identity_id=identity_id,
        )
    result = manual_borrow_diagnostic(
        service, item, provider_id=item.source_provider,
        docbase=policy.docbase, primary_id=policy.primary_borrower_id,
        identity_id=identity_id,
        source_current=lambda service_, item_, identity: (
            _selected_primary_source_current(service_, item_, identity, policy)
            and resolved_manual_borrow_policy(service_, item_) == policy
        ),
    )
    return result.get("local_action_possible") is True


def manual_borrow_action(
    service, item: SearchResult, *, expected_identity_id: str,
    confirmed: bool = False, expected_context_epoch: int | None = None,
    expected_account=None, additional_scope_current=None,
) -> dict[str, object]:
    """Route only into Core's existing one-shot manual_borrow, never borrow()."""
    from .manual_library_borrow import manual_borrow
    policy = resolved_manual_borrow_policy(service, item)
    if policy is None:
        return {
            "status": "blocked", "reason_code": "borrow_scope_unverified",
            "provider_verified": False, "retry": "manual_only",
        }

    def still_bound() -> bool:
        return (
            (additional_scope_current is None or additional_scope_current() is True)
            and resolved_manual_borrow_policy(service, item) == policy
            and _selected_primary_source_current(
                service, item, expected_identity_id, policy,
            ) is True
        )

    if not still_bound():
        return {
            "status": "blocked", "reason_code": "borrow_scope_changed",
            "provider_verified": False, "retry": "manual_only",
        }
    return manual_borrow(
        service, item, provider_id=item.source_provider,
        docbase=policy.docbase, primary_id=policy.primary_borrower_id,
        expected_identity_id=expected_identity_id, confirmed=confirmed,
        expected_context_epoch=expected_context_epoch,
        expected_account=expected_account, verification=policy.verification,
        observed_loan_account_alias=policy.observed_loan_account_alias,
        additional_scope_current=still_bound,
    )


def prepare_confirmed_manual_borrow(
    service, item: SearchResult, *, identity_id: str, account_id: str, confirmed: bool,
):
    """Passive explicit admission. The one-shot closure owns the original Core.

    It is an internal mandate, not a new permission source or transport DTO.
    The configured provider policy and existing CAS executor remain authoritative.
    """
    from threading import Lock
    from ..models import Account
    from .search_orchestration import capture_search_read_scope
    from .borrow_action_scope import capture_borrow_action_scope
    from .manual_library_borrow import manual_borrow, _result
    try:
        if (confirmed is not True or type(item) is not SearchResult
                or type(account_id) is not str or len(account_id) > 1024
                or any(ch in account_id for ch in "\x00\r\n")
                or type(item.metadata) is not dict
                or item.metadata.get("catalogue_access") == "shared_read_only"
                or item.metadata.get("search_provenance") == "known_catalog_stale"):
            return None
        observed_borrower = item.source_account_id
        if observed_borrower is not None and (
                type(observed_borrower) is not str or observed_borrower != account_id):
            return None
        scope = capture_search_read_scope(service, identity_id)
        policy = resolved_manual_borrow_policy(service, item, reader_scope=scope)
        if policy is None or not _selected_primary_source_current(service, item, identity_id, policy):
            return None
        if policy.primary_borrower_id is not None and policy.primary_borrower_id != account_id:
            return None
        accounts = [
            a for a in service.accounts
            if type(a) is Account and a.is_primary is True
            and (a.identity_id, a.provider_id, a.credential_id, a.unique_id) == (
                identity_id, item.source_provider, item.source_credential, account_id)
        ]
        if len(accounts) != 1:
            return None
        account = accounts[0]
        session = service._sessions.get(service._session_key(item.source_provider, item.source_credential))
        lease = capture_borrow_action_scope(
            service, item, account, session, expected_identity_id=identity_id,
            reader_scope=scope,
        )
        if lease is None:
            return None

        def current():
            try:
                return (lease.current(service)
                        and item.source_account_id == observed_borrower
                        and type(item.source_account_id) is type(observed_borrower)
                        and type(item.metadata) is dict
                        and item.metadata.get("catalogue_access") != "shared_read_only"
                        and item.metadata.get("search_provenance") != "known_catalog_stale"
                        and resolved_manual_borrow_policy(service, item, reader_scope=scope) == policy
                        and _selected_primary_source_current(service, item, identity_id, policy)
                        and lease.current(service))
            except Exception:
                return False

        if not current():
            return None
        lock = Lock()
        used = False

        def execute():
            nonlocal used
            with lock:
                if used:
                    return _result("blocked", "borrow_confirmation_consumed")
                used = True
            if not current():
                return _result("blocked", "borrow_scope_changed")
            return manual_borrow(
                service, item, provider_id=account.provider_id, docbase=policy.docbase,
                primary_id=policy.primary_borrower_id, expected_identity_id=identity_id,
                confirmed=True, expected_account=account, reader_scope=scope,
                verification=policy.verification,
                observed_loan_account_alias=policy.observed_loan_account_alias,
                additional_scope_current=current,
            )
        return execute
    except Exception:
        return None
