"""Explicitly confirmed legacy direct downloads, without navigation authority.

The old supplier ABI accepts an item and a directory, not an account. Only
a single, exact persisted borrower is eligible. A virtual/shared catalogue
session without a durable owner is intentionally not promoted to an action.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from inspect import getattr_static
from threading import Lock

from ..models import Account, SearchResult
from ..providers import ProviderConfigurationError
from .search_orchestration import SearchReadScope


def _text(value, *, empty=False):
    return (type(value) is str and (empty or bool(value))
            and len(value) <= 1024
            and not any(ord(c) < 32 or ord(c) == 127 for c in value))


def _legacy_shape(item, session):
    return (
        type(item) is SearchResult
        and type(item.metadata) is dict
        and item.metadata.get("search_provenance") != "known_catalog_stale"
        and item.metadata.get("catalogue_access") != "shared_read_only"
        and not (item.source_collection == "library"
                 and item.medium == "digital" and item.download_url is None)
        and getattr_static(session, "selected_purchase_branch", None) is None
        and getattr_static(session, "private_inventory_row_contract", None) is None
        and getattr_static(session, "legacy_direct_get_retired", None) is not True
    )


def _single_account(service, session, coords):
    identity, provider, credential, account_id = coords
    rows = [a for a in service.accounts
            if (getattr(a, "provider_id", None), getattr(a, "credential_id", None))
            == (provider, credential)]
    if len(rows) != 1 or type(rows[0]) is not Account:
        return None
    account = rows[0]
    if (account.identity_id, account.unique_id) != (identity, account_id):
        return None
    upstream = session.accounts
    if type(upstream) not in (list, tuple) or len(upstream) != 1:
        return None
    # The legacy dispatch has no borrower argument; do not guess between
    # linked accounts or a replaced upstream object.
    return account if upstream[0] is account else None


@dataclass(frozen=True, slots=True, repr=False)
class LegacyDirectAdmission:
    scope: SearchReadScope
    coordinates: tuple[str, str, str, str]
    session: object
    account: Account
    connection_epoch: int
    _item: SearchResult
    _lock: object = field(default_factory=Lock, compare=False)
    _used: list = field(default_factory=lambda: [False], compare=False)

    @property
    def identity_id(self):
        return self.coordinates[0]

    def current(self, service, item=None):
        try:
            identity, provider, credential, account_id = self.coordinates
            return (
                self.scope.current(service)
                and (item is None or (type(item) is SearchResult and item == self._item))
                and _legacy_shape(self._item, self.session)
                and service._sessions.get(service._session_key(provider, credential)) is self.session
                and getattr_static(self.session, "provider_id", None) == provider
                and getattr_static(self.session, "credential_id", None) == credential
                and getattr_static(self.session, "connected", False) is True
                and getattr_static(self.session, "_closed", False) is False
                and _single_account(service, self.session, self.coordinates) is self.account
                and service._provider_enabled_for_identity(provider, identity) is True
                and service.can_direct_download_for_scope(self.scope, self._item) is True
                and service.storage.provider_scope_account_bound(
                    identity_id=identity, provider_id=provider,
                    credential_id=credential, account_id=account_id,
                ) is True
                and service.storage.provider_connection_epoch_current(
                    provider, credential, identity, self.connection_epoch,
                ) is True
                and self.scope.current(service)
            )
        except Exception:
            return False

    def claim(self, service):
        with self._lock:
            if self._used[0]:
                raise ProviderConfigurationError("legacy_direct_confirmation_consumed")
            self._used[0] = True
        if not self.current(service):
            raise ProviderConfigurationError("legacy_direct_source_revoked")
        return deepcopy(self._item)


def capture_legacy_direct(service, item, *, scope, account_id, confirmed):
    """Passive admission. Does not call a supplier, create a file or grant rights."""
    if (confirmed is not True or type(scope) is not SearchReadScope
            or not scope.current(service) or type(item) is not SearchResult
            or not _text(account_id, empty=True)
            or not all(_text(value) for value in
                       (item.source_provider, item.source_credential, item.record_id, item.id))
            or (item.source_account_id is not None
                and (not _text(item.source_account_id, empty=True)
                     or item.source_account_id != account_id))):
        raise ProviderConfigurationError("legacy_direct_admission_unavailable")
    identity, provider, credential = scope.identity_id, item.source_provider, item.source_credential
    coords = (identity, provider, credential, account_id)
    try:
        session = service._sessions.get(service._session_key(provider, credential))
        if session is None or not _legacy_shape(item, session):
            raise ValueError()
        account = _single_account(service, session, coords)
        epoch = service.storage.provider_connection_epoch(provider, credential, identity)
        if account is None or type(epoch) is not int or epoch < 1:
            raise ValueError()
        target = LegacyDirectAdmission(scope, coords, session, account, epoch, deepcopy(item))
        if not target.current(service):
            raise ValueError()
        return target
    except Exception:
        raise ProviderConfigurationError("legacy_direct_admission_unavailable") from None
