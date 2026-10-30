"""#3171: provider-neutral, exact-right selected offer preflight under Core Sync.

The existing Sync owns the lock, admitted typed A and durable consent. This
module may *read* a new offer for one currently authorized SourceKey, but it
never GETs content, invokes ADEPT, creates a receipt or publishes SQLite B.
"""
from __future__ import annotations

from .source_context import source_context_epoch

from dataclasses import replace
from inspect import getattr_static
import logging
from pathlib import Path
from typing import Callable

from ..locking import InterProcessLock
from ..models import Account
from ..paths import acquisition_lock_file
from ..plugin_api.errors import Cancelled
from ..plugin_api.provider_rights_observation import ProviderRightsSnapshot
from .convergence_verified_presence import plan_existing_verified_presence
from .source_convergence import SourceKey

_LOG = logging.getLogger("cifereca")


def qualify_selected_offers(
    service, account: Account, session: object, snapshot: ProviderRightsSnapshot,
    *, admitted_epoch: int, shared_lock: InterProcessLock,
    library_root: Path, persisted_consent: frozenset[SourceKey],
    consent_still_current: Callable[[SourceKey], bool],
    cancelled: Callable[[], bool] = lambda: False,
) -> ProviderRightsSnapshot:
    """Upgrade only consented, absent, exactly selected offers; otherwise deny.

    An offer remains A, not B or an admission to publish. A future execution
    callback must independently revalidate this exact offer under the Core
    executor's existing pre/post-GET, SHA and SQLite publication guards.
    """
    if (type(account) is not Account
            or type(snapshot) is not ProviderRightsSnapshot
            or type(admitted_epoch) is not int
            or type(shared_lock) is not InterProcessLock
            or not isinstance(library_root, Path)
            or type(persisted_consent) is not frozenset
            or any(type(k) is not SourceKey for k in persisted_consent)
            or not callable(consent_still_current) or not callable(cancelled)):
        raise ValueError("canonical_offer_core_admission_required")

    scope = snapshot.scope
    coordinates = (scope.identity_id, scope.provider_id,
                   scope.credential_id, scope.account_id)
    if (account.identity_id, account.provider_id,
            account.credential_id, account.unique_id) != coordinates:
        raise ValueError("canonical_offer_scope_mismatch")
    if snapshot.status not in ("success", "partial") or not snapshot.rights:
        return snapshot

    # The optional ABI is producer-neutral and remains absent on older
    # installed providers. Do not call any legacy download/loans/library
    # fallback, and never try this preflight without exact persisted consent.
    if not any(SourceKey(scope, row.edition_id, row.right_id,
                         row.kind, snapshot.branch) in persisted_consent
               for row in snapshot.rights if row.provider_attested
               and row.delivery == "unknown"):
        return snapshot
    raw_reader = getattr_static(session, "rights_selected_delivery_offer_snapshot", None)
    if raw_reader is None:
        return snapshot
    if not callable(raw_reader):
        raise ValueError("invalid_canonical_selected_offer_reader")
    # Require the already-held Core acquisition lease before the first
    # extra provider I/O. A provider without this optional ABI is a no-op.
    if (shared_lock._handle is None or
            shared_lock.path.resolve() != acquisition_lock_file().resolve()):
        raise ValueError("canonical_offer_core_lock_required")

    try:
        initial_storage = service.storage
        initial_accounts = service.accounts
        initial_sessions = service._sessions
    except AttributeError as exc:
        raise ValueError("canonical_offer_core_context_required") from exc

    def current() -> bool:
        try:
            if (service.storage is not initial_storage
                    or service.accounts is not initial_accounts
                    or service._sessions is not initial_sessions
                    or source_context_epoch(service) != admitted_epoch
                    or (account.identity_id, account.provider_id,
                        account.credential_id, account.unique_id) != coordinates):
                return False
            matches = [row for row in service.accounts
                       if type(row) is Account
                       and (row.identity_id, row.provider_id,
                            row.credential_id, row.unique_id) == coordinates]
            # The selected-offer read is itself private provider I/O. Check
            # the live upstream borrower before and after that read, rather
            # than relying only on the later delivery executor's GET fence.
            session_accounts = session.accounts
            if (not isinstance(session_accounts, (list, tuple))
                    or sum(
                        type(row) is Account
                        and (row.identity_id, row.provider_id,
                             row.credential_id, row.unique_id) == coordinates
                        for row in session_accounts
                    ) != 1):
                return False
            registered = [s for s in service._sessions.values()
                          if getattr(s, "provider_id", None) == scope.provider_id
                          and getattr(s, "credential_id", None) == scope.credential_id]
            if (len(matches) != 1 or matches[0] is not account
                    or len(registered) != 1 or registered[0] is not session
                    or service._sessions.get(service._session_key(
                        scope.provider_id, scope.credential_id)) is not session
                    or getattr(session, "connected", None) is not True
                    or getattr(session, "_closed", False) is not False
                    or service.sync_source_enabled(account) is not True
                    or initial_storage.identity_for_connection(
                        scope.provider_id, scope.credential_id) != scope.identity_id):
                return False
            connections = initial_storage.provider_connections(
                identity_id=scope.identity_id)
            return sum(row.get("provider_id") == scope.provider_id
                       and row.get("credential_id") == scope.credential_id
                       and row.get("enabled") is True for row in connections) == 1
        except (AttributeError, TypeError, ValueError, KeyError):
            return False

    def consent_current(key: SourceKey) -> bool:
        try:
            return consent_still_current(key) is True
        except Exception:
            return False

    if cancelled():
        raise Cancelled("canonical_offer_preflight_cancelled")
    if not current():
        raise ValueError("canonical_offer_source_rebound")

    # A verified provider OriginLink + correctly hashed Placement is already
    # satisfied. Even a selected preflight must not issue another private GET.
    plan = plan_existing_verified_presence(
        initial_storage, snapshot, library_root=library_root)
    satisfied = {decision.source for decision in plan.decisions
                 if decision.provider_satisfied}

    rights = list(snapshot.rights)
    for i, row in enumerate(snapshot.rights):
        key = SourceKey(scope, row.edition_id, row.right_id, row.kind,
                        snapshot.branch)
        if (not row.provider_attested or row.delivery != "unknown"
                or key not in persisted_consent or key in satisfied):
            continue
        if cancelled():
            raise Cancelled("canonical_offer_preflight_cancelled")
        if not current():
            raise ValueError("canonical_offer_source_rebound")
        if not consent_current(key):
            continue
        try:
            selected = session.rights_selected_delivery_offer_snapshot(
                account, edition_id=key.edition_id, right_id=key.right_id,
                kind=key.kind, branch=key.branch, explicit_user_action=True,
            )
        except Cancelled:
            raise
        except Exception:
            # Provider preflight is optional. No invented offer, GET, or right
            # follows a refusal; no raw exception or private URL is logged.
            if not current():
                raise ValueError("canonical_offer_source_rebound") from None
            _LOG.warning("TRACE selected offer unavailable provider_id=%s",
                         scope.provider_id)
            continue
        if cancelled():
            raise Cancelled("canonical_offer_preflight_cancelled")
        if not current():
            raise ValueError("canonical_offer_source_rebound")
        if not consent_current(key):
            continue
        if (type(selected) is not ProviderRightsSnapshot
                or selected.scope != scope or selected.branch != snapshot.branch
                or selected.status not in ("partial", "success")
                or selected.coverage != "partial"
                or len(selected.rights) != 1
                or selected.rights[0].edition_id != key.edition_id
                or selected.rights[0].right_id != key.right_id
                or selected.rights[0].kind != key.kind
                or not selected.rights[0].provider_attested):
            continue
        rights[i] = selected.rights[0]
    return replace(snapshot, rights=tuple(rights))


__all__ = ["qualify_selected_offers"]
