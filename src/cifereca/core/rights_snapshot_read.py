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
from cifereca.plugin_api.downloadable_resources import (
    ResourceSnapshot, as_legacy_rights_snapshot,
)

_LOG = logging.getLogger("cifereca")
# Bounded positive-only pagination; page count also limits private provider I/O.
_MAX_INVENTORY_ROWS = 10_000
_MAX_INVENTORY_PAGES = 128


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
        # A source can be disabled while a private inventory request is in
        # flight without replacing the account/session or bumping the Core
        # context epoch. Recheck before the first GET, after each provider
        # reply and before following a pagination cursor. A stale positive A
        # batch must not survive a source disable.
        try:
            if service.sync_source_enabled(account) is not True:
                return False
        except Exception:
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
        # A connected credential may outlive a deleted or rebound upstream
        # borrower. A connection-to-identity match alone must not authorize
        # even the FIRST private inventory GET or another pagination page.
        # Require the exact persisted borrower joined to the active connection
        # on every pre/post-provider-read check, independently of session.accounts.
        try:
            exact_bound = storage.provider_scope_account_bound(
                identity_id=scope.identity_id, provider_id=scope.provider_id,
                credential_id=scope.credential_id, account_id=scope.account_id,
            ) is True
        except Exception:
            # SQL/backend failures may include private account coordinates.
            # Emit only a fixed diagnostic, with no provider exception text.
            exact_bound = False
        if not exact_bound:
            _LOG.warning(
                "TRACE rights snapshot read failed "
                "code=canonical_rights_core_binding_changed"
            )
        return exact_bound

    reader_kind = "optional_unavailable"
    try:
        if cancelled():
            return unavailable("cancelled")
        if not still_bound():
            return unavailable()
        # An opening source Sync may supply its *just-read* exact typed
        # inventory for this same account and session. This is a one-phase
        # ContextVar lease, never a TTL cache or a general rights shortcut.
        # Only providers with an explicit pure projection method can use it.
        from .same_cycle_loan_inventory import current_scoped_loan_inventory
        same_cycle_inventory = current_scoped_loan_inventory(service, account, session)
        shared_reader = "downloadable_resource_snapshot_from_loan_inventory"
        share = (
            same_cycle_inventory is not None
            and getattr_static(session, shared_reader, None) is not None
        )
        reader_name = (
            shared_reader if share else
            "downloadable_resource_snapshot"
            if getattr_static(session, "downloadable_resource_snapshot", None) is not None
            else "rights_observation_snapshot"
        )
        raw_reader = getattr_static(session, reader_name, None)
        if raw_reader is None:
            return unavailable()
        if not callable(raw_reader):
            raise ValueError("invalid_rights_reader")
        reader_kind = reader_name
        reader = object.__getattribute__(session, reader_name)
        if not callable(reader):
            raise ValueError("invalid_rights_reader")
        def validated(raw):
            if (reader_name in (
                    "downloadable_resource_snapshot",
                    "downloadable_resource_snapshot_from_loan_inventory",
            ) and type(raw) is not ResourceSnapshot):
                raise ValueError("invalid_downloadable_snapshot")
            if type(raw) is ResourceSnapshot:
                if raw.scope != scope:
                    raise ValueError("invalid_downloadable_snapshot_scope")
                raw = as_legacy_rights_snapshot(raw)
            if type(raw) is not ProviderRightsSnapshot or raw.scope != scope:
                raise ValueError("invalid_rights_snapshot_scope")
            return raw

        result = validated(
            reader(account, loan_inventory=same_cycle_inventory)
            if share else reader(account)
        )
        if cancelled():
            return unavailable("cancelled")
        if not still_bound():
            return unavailable()
        if not result.resume_cursor:
            # Unpaged providers preserve the original Core result contract.
            return result

        # Collect only positive observations from bounded, exact-account
        # pages. Even reaching the last cursor grants no negative authority.
        first = result
        accepted = list(result.rights)
        known = {(r.edition_id, r.right_id, r.kind) for r in accepted}
        visited = set()
        cursor = result.resume_cursor
        while (cursor and len(visited) < _MAX_INVENTORY_PAGES
               and len(accepted) < _MAX_INVENTORY_ROWS):
            if cursor in visited:
                break
            visited.add(cursor)
            if cancelled():
                return unavailable("cancelled")
            if not still_bound():
                return unavailable()
            try:
                page = validated(reader(account, resume_cursor=cursor))
            except Cancelled:
                return unavailable("cancelled")
            except ValueError as exc:
                if str(exc) in (
                    "invalid_downloadable_snapshot_scope",
                    "invalid_rights_snapshot_scope",
                ):
                    # An attestation for another account invalidates the
                    # entire old lease, not merely the current cursor.
                    return unavailable()
                _LOG.warning("TRACE rights pagination code=page_unavailable")
                break
            except Exception:
                # Preserve earlier positives; never log a private exception.
                _LOG.warning("TRACE rights pagination code=page_unavailable")
                break
            if cancelled():
                return unavailable("cancelled")
            if not still_bound():
                return unavailable()
            if page.status == "cancelled":
                return unavailable("cancelled")
            if page.status == "failed":
                # A failed continuation legitimately has no branch and no
                # rows. Preserve the previously observed positives only.
                break
            if page.branch != first.branch:
                # A positive page for a different branch invalidates A.
                return unavailable()
            keys = [(r.edition_id, r.right_id, r.kind) for r in page.rights]
            if (any(k in known for k in keys)
                    or (page.resume_cursor and not page.rights)
                    or len(accepted) + len(page.rights) > _MAX_INVENTORY_ROWS):
                break
            accepted.extend(page.rights)
            known.update(keys)
            cursor = page.resume_cursor
        return ProviderRightsSnapshot(
            scope=scope, rights=tuple(accepted), branch=first.branch,
            status="partial", coverage="partial", pagination_complete=False,
            observed_at=first.observed_at, snapshot_ref=first.snapshot_ref,
            resume_cursor=cursor,
        )
    except Cancelled:
        return unavailable("cancelled")
    except Exception:
        # Provider exceptions can contain signed URLs, private account IDs,
        # paths and response bodies. Only report a closed, non-sensitive code.
        _LOG.warning(
            "TRACE rights snapshot read failed code=provider_error reader_kind=%s",
            reader_kind,
        )
        return unavailable()


__all__ = ["read_account_rights_snapshot"]
