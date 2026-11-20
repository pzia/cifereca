"""P0 #5833: source-scoped admission for an explicit private Search download.

The Search click is the user gesture. This module performs only passive
Core account/session/SQLite checks, never a provider GET, borrow or download.
Execution later revalidates the same exact source in the canonical executor.
"""
from __future__ import annotations

from dataclasses import replace
from inspect import getattr_static

from ..models import Account, SearchResult
from .search_orchestration import SearchReadScope
from .source_context import source_context_epoch
from .private_inventory_row_scope import private_row_account_matches
from .selected_source_offer import (
    SourceOfferTarget, _ConfirmedOfferUse, _resolve, select_source_offer_target,
)


def _validate_selected_item(item, identity_id):
    if (type(item) is not SearchResult
            or type(identity_id) is not str or not identity_id
            or type(item.id) is not str or item.id != item.record_id
            or type(item.record_id) is not str or not item.record_id
            or type(item.source_provider) is not str
            or not item.source_provider
            or type(item.source_credential) is not str
            or not item.source_credential
            or item.source_collection != "library"
            or item.medium != "digital"
            or item.download_url is not None
            or item.owned is not True or item.downloadable is not True
            or type(item.metadata) is not dict
            or item.metadata.get("search_provenance") == "known_catalog_stale"):
        raise ValueError("selected_search_source_unproven")


def capture_admitted_selected_search_offer(
    service, item: SearchResult, *, identity_id: str,
    account_epoch: int, source_epoch: int,
) -> SourceOfferTarget:
    """Pin one private candidate at click time; never select in a worker."""
    _validate_selected_item(item, identity_id)
    if (type(account_epoch) is not int or account_epoch < 0
            or type(source_epoch) is not int or source_epoch < 0):
        raise ValueError("selected_search_source_unproven")
    if source_context_epoch(service) != source_epoch:
        raise ValueError("selected_search_source_revoked")
    target = select_source_offer_target(
        service, provider_id=item.source_provider,
        credential_id=item.source_credential,
        item_id=item.record_id, identity_id=identity_id,
        expected_epoch=account_epoch,
    )
    if (type(target) is not SourceOfferTarget
            or not private_row_account_matches(item, target.account_id)):
        raise ValueError("selected_search_source_unavailable")
    # The navigation epoch is checked on the GTK click, never inside a
    # deferred provider worker. Source generation and exact binding persist.
    admitted = replace(
        target, selection_epoch=None, source_epoch=source_epoch,
        _confirmed_use=_ConfirmedOfferUse(),
    )
    if (source_context_epoch(service) != source_epoch
            or _resolve(service, admitted)[0] is not admitted):
        raise ValueError("selected_search_source_revoked")
    return admitted


def capture_confirmed_selected_offer(
    service, item: SearchResult, *, reader_scope: SearchReadScope,
    account_id: str, confirmed: bool,
) -> SourceOfferTarget:
    """Headless explicit user admission; a read scope alone grants nothing.

    The caller must confirm this exact item/account. No provider request,
    durable right or source-wide consent is produced by this function.
    """
    if (confirmed is not True or type(reader_scope) is not SearchReadScope
            or not reader_scope.current(service)
            or type(account_id) is not str or not account_id
            or len(account_id) > 1024
            or any(c in account_id for c in "\x00\r\n")):
        raise ValueError("selected_search_confirmation_required")
    identity_id = reader_scope.identity_id
    _validate_selected_item(item, identity_id)
    if (not all(type(value) is str and 0 < len(value) <= 1024
                and not any(c in value for c in "\x00\r\n")
                for value in (item.source_provider, item.source_credential, item.record_id))
            or not private_row_account_matches(item, account_id)):
        raise ValueError("selected_search_source_unavailable")
    matches = [
        account for account in service.accounts
        if type(account) is Account
        and (account.identity_id, account.provider_id,
             account.credential_id, account.unique_id) == (
                 identity_id, item.source_provider, item.source_credential, account_id,
             )
    ]
    if len(matches) != 1:
        raise ValueError("selected_search_source_unavailable")
    session = service._sessions.get(
        service._session_key(item.source_provider, item.source_credential)
    )
    branch = getattr_static(session, "selected_purchase_branch", None)
    if type(branch) is not str or not branch or len(branch) > 128:
        raise ValueError("selected_search_source_unavailable")
    target = SourceOfferTarget(
        identity_id, item.source_provider, item.source_credential, account_id,
        item.record_id, branch, session, source_epoch=reader_scope.source_epoch,
        reader_scope=reader_scope, _confirmed_use=_ConfirmedOfferUse(),
    )
    _resolve(service, target)
    return target


__all__ = ["capture_admitted_selected_search_offer", "capture_confirmed_selected_offer"]
