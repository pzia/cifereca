"""#3171: Core admission of optional #3252 provider rights snapshots.

One explicit read of the selected account; zero fallback inventory/downloads.
A provider's snapshot is positive evidence only, not a right/placement write or
a download permit. Session, registered account, connection and epoch are
rechecked after provider I/O; a stale result is discarded in full.
"""
from __future__ import annotations

from .source_context import source_context_epoch

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
        epoch = source_context_epoch(service)
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
        if source_context_epoch(service) != epoch:
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
        # A second same-scoped Account may be appended to the *same* list
        # while the provider reads. Counting only the original object would
        # miss that ambiguity, even though the epoch/list identity is stable.
        current_accounts = [
            row for row in accounts
            if type(row) is Account and (
                row.identity_id, row.provider_id,
                row.credential_id, row.unique_id,
            ) == (scope.identity_id, scope.provider_id,
                  scope.credential_id, scope.account_id)
        ]
        if len(current_accounts) != 1 or current_accounts[0] is not account:
            return False
        # The live provider session may switch its authenticated borrower
        # without changing the Core registry, Python object or epoch.
        # Check the current upstream account BEFORE and AFTER the private
        # rights inventory read; a sibling account is not the selected one.
        try:
            upstream_accounts = session.accounts
        except Exception:
            return False
        if (not isinstance(upstream_accounts, (tuple, list))
                or sum(
                    type(row) is Account
                    and (row.identity_id, row.provider_id,
                         row.credential_id, row.unique_id) == (
                            scope.identity_id, scope.provider_id,
                            scope.credential_id, scope.account_id)
                    for row in upstream_accounts
                ) != 1):
            return False
        # Similarly, a second session can be inserted into the same mapping
        # without replacing the original selected instance. Neither can
        # authorize a positive observation after a mid-read owner race.
        registered = [
            value for value in sessions.values()
            if (getattr(value, "provider_id", None) == scope.provider_id
                and getattr(value, "credential_id", None) == scope.credential_id)
        ]
        if len(registered) != 1 or registered[0] is not session:
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
