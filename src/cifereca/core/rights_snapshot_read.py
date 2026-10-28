"""#3171: Core admission of optional #3252 provider rights snapshots.

One explicit read of the selected account; zero fallback inventory/downloads.
A provider's snapshot is positive evidence only, not a right/placement write or
a download permit. Session, registered account, connection and epoch are
rechecked after provider I/O; a stale result is discarded in full.
"""
from __future__ import annotations

from inspect import getattr_static
import logging

from cifereca.models import Account
from cifereca.plugin_api.errors import Cancelled
from cifereca.plugin_api.provider_rights_observation import (
    ProviderRightsScope, ProviderRightsSnapshot,
)

_LOG = logging.getLogger("cifereca")


def read_account_rights_snapshot(service, account: Account, *,
                                 cancelled=lambda: False) -> ProviderRightsSnapshot:
    """Explicit opt-in account I/O, never invoked by a passive Core projection.

    The caller owns source enablement, task cancellation and exclusive sync
    admission. No provider is selected by name; the only accepted session is
    the currently registered, connected provider+credential for this account.
    A missing optional reader is not replaced by library(), loans() or GET.
    """
    if type(account) is not Account or not callable(cancelled):
        raise ValueError("invalid_rights_reader_request")
    scope = ProviderRightsScope(
        account.identity_id, account.provider_id,
        account.credential_id, account.unique_id,
    )

    def unavailable(status: str = "failed") -> ProviderRightsSnapshot:
        return ProviderRightsSnapshot(scope=scope, status=status)

    try:
        epoch = service._account_context_epoch
        accounts = service.accounts
        sessions = service._sessions
        storage = service.storage
    except AttributeError as exc:
        raise ValueError("invalid_rights_reader_service") from exc
    if type(sessions) is not dict or not isinstance(accounts, (tuple, list)):
        raise ValueError("invalid_rights_reader_service")

    matches = [
        row for row in accounts
        if type(row) is Account and (
            row.identity_id, row.provider_id,
            row.credential_id, row.unique_id,
        ) == (scope.identity_id, scope.provider_id,
              scope.credential_id, scope.account_id)
    ]
    if len(matches) != 1 or matches[0] is not account:
        return unavailable()

    registered = [
        session for session in sessions.values()
        if getattr(session, "provider_id", None) == scope.provider_id
        and getattr(session, "credential_id", None) == scope.credential_id
    ]
    if len(registered) != 1:
        return unavailable()
    session = registered[0]

    def still_bound() -> bool:
        if service._account_context_epoch != epoch:
            return False
        if service.accounts is not accounts or service._sessions is not sessions:
            return False
        if service.storage is not storage:
            return False
        if (
            account.identity_id, account.provider_id,
            account.credential_id, account.unique_id,
        ) != (scope.identity_id, scope.provider_id,
              scope.credential_id, scope.account_id):
            return False
        if sum(
            row is account for row in service.accounts
            if type(row) is Account and (
                row.identity_id, row.provider_id,
                row.credential_id, row.unique_id,
            ) == (scope.identity_id, scope.provider_id,
                  scope.credential_id, scope.account_id)
        ) != 1:
            return False
        if not any(value is session for value in sessions.values()):
            return False
        if getattr_static(session, "_closed", False) not in (None, False):
            return False
        if not (getattr(session, "connected", None) is True
                and getattr(session, "provider_id", None) == scope.provider_id
                and getattr(session, "credential_id", None) == scope.credential_id):
            return False
        return (
            storage.identity_for_connection(
                scope.provider_id, scope.credential_id,
            ) == scope.identity_id
        )

    reader_kind = "optional_unavailable"
    try:
        if cancelled():
            return unavailable("cancelled")
        if not still_bound():
            return unavailable()
        raw_reader = getattr_static(session, "rights_observation_snapshot", None)
        if raw_reader is None:
            return unavailable()
        if not callable(raw_reader):
            raise ValueError("invalid_rights_reader")
        reader_kind = "typed"
        reader = object.__getattribute__(session, "rights_observation_snapshot")
        if not callable(reader):
            raise ValueError("invalid_rights_reader")
        result = reader(account)
        # In-place account/session rebind and cancelled reads cannot publish
        # a still-well-formed but now foreign snapshot as current evidence.
        if cancelled():
            return unavailable("cancelled")
        if not still_bound():
            return unavailable()
        if type(result) is not ProviderRightsSnapshot or result.scope != scope:
            raise ValueError("invalid_rights_snapshot_scope")
        return ProviderRightsSnapshot(
            scope=scope, rights=result.rights, status=result.status,
            coverage=result.coverage, pagination_complete=result.pagination_complete,
            branch=result.branch, observed_at=result.observed_at,
            snapshot_ref=result.snapshot_ref, resume_cursor=result.resume_cursor,
            schema_version=result.schema_version,
        )
    except Cancelled:
        return unavailable("cancelled")
    except Exception:
        _LOG.warning(
            "TRACE rights snapshot read failed provider_id=%s identity_id=%s "
            "credential_id=%s account_id=%s reader_kind=%s",
            scope.provider_id, scope.identity_id, scope.credential_id,
            scope.account_id, reader_kind, exc_info=True,
        )
        return unavailable()


__all__ = ["read_account_rights_snapshot"]
