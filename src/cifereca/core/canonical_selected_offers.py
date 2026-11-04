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
import re
from pathlib import Path
from typing import Callable

from ..locking import InterProcessLock
from ..models import Account
from ..paths import acquisition_lock_file
from ..plugin_api.errors import Cancelled
from ..plugin_api.provider_rights_observation import ProviderRightsSnapshot
from ..plugin_api.downloadable_resources import ResourceSnapshot, as_legacy_rights_snapshot
from .convergence_verified_presence import plan_existing_verified_presence
from .source_convergence import SourceKey

_LOG = logging.getLogger("cifereca")


def qualify_selected_offers(
    service, account: Account, session: object, snapshot: ProviderRightsSnapshot,
    *, admitted_epoch: int, shared_lock: InterProcessLock,
    library_root: Path, persisted_consent: frozenset[SourceKey] | None,
    consent_still_current: Callable[[SourceKey], bool],
    expected_source_policy_revision: str | None = None,
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
            or (persisted_consent is None and expected_source_policy_revision is None)
            or (persisted_consent is not None and type(persisted_consent) is not frozenset)
            or any(type(k) is not SourceKey for k in (persisted_consent or ()))
            or not callable(consent_still_current) or not callable(cancelled)):
        raise ValueError("canonical_offer_core_admission_required")

    scope = snapshot.scope
    coordinates = (scope.identity_id, scope.provider_id,
                   scope.credential_id, scope.account_id)
    if (account.identity_id, account.provider_id,
            account.credential_id, account.unique_id) != coordinates:
        raise ValueError("canonical_offer_scope_mismatch")
    if persisted_consent is None:
        from .convergence_execution import _source_policy_generation_current
        if not _source_policy_generation_current(
            service.storage, scope, expected_source_policy_revision
        ):
            return snapshot
    if snapshot.status not in ("success", "partial") or not snapshot.rights:
        return snapshot

    # The optional ABI is producer-neutral and remains absent on older
    # installed providers. Do not call any legacy download/loans/library
    # fallback, and never try this preflight without exact persisted consent.
    if not any(SourceKey(scope, row.edition_id, row.right_id,
                         row.kind, snapshot.branch) in (persisted_consent or ())
               or (persisted_consent is None and row.provider_attested
                   and row.delivery == "unknown")
               for row in snapshot.rights if row.provider_attested
               and row.delivery == "unknown"):
        return snapshot
    reader_name = (
        "downloadable_selected_offer_snapshot"
        if getattr_static(session, "downloadable_selected_offer_snapshot", None) is not None
        else "rights_selected_delivery_offer_snapshot"
    )
    raw_reader = getattr_static(session, reader_name, None)
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
                    or not initial_storage.provider_scope_account_bound(
                        identity_id=scope.identity_id,
                        provider_id=scope.provider_id,
                        credential_id=scope.credential_id,
                        account_id=scope.account_id,
                    )):
                return False
            return True
        except (AttributeError, TypeError, ValueError, KeyError):
            return False

    def consent_current(key: SourceKey) -> bool:
        try:
            # A source-level ON grant is bound to its exact generation, not
            # merely to the current boolean value. OFF -> ON while a private
            # selected-offer request is in flight must not let the old Sync
            # continue reading or accept the returned offer (ABA).
            if expected_source_policy_revision is not None:
                from .convergence_execution import _source_policy_generation_current
                if not _source_policy_generation_current(
                    initial_storage, scope, expected_source_policy_revision
                ):
                    return False
            return consent_still_current(key) is True
        except Exception:
            # Failed policy reads are not authorization for provider I/O.
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
                or (persisted_consent is not None and key not in persisted_consent) or key in satisfied):
            continue
        if cancelled():
            raise Cancelled("canonical_offer_preflight_cancelled")
        if not current():
            raise ValueError("canonical_offer_source_rebound")
        if not consent_current(key):
            continue
        try:
            selected = getattr(session, reader_name)(
                account, edition_id=key.edition_id,
                **({"resource_id": key.right_id}
                   if reader_name == "downloadable_selected_offer_snapshot"
                   else {"right_id": key.right_id}),
                kind=key.kind, branch=key.branch, explicit_user_action=True,
            )
        except Cancelled:
            raise
        except Exception as error:
            # A selected preflight may refuse BEFORE the provider's own
            # descriptor instrumentation. Record the *actual* stable code
            # rather than turning every pending book into a guessed HTTP 403.
            # Only closed ProviderCapabilityError codes are accepted from the
            # message; arbitrary upstream error bodies or signed URLs are
            # never interpreted as a diagnostic classification.
            if not current():
                raise ValueError("canonical_offer_source_rebound") from None
            from ..plugin_api import ProviderCapabilityError
            reason = "provider_preflight_exception"
            if type(error) is ProviderCapabilityError:
                candidate = str(error)
                if re.fullmatch(r"[a-z][a-z0-9_]{0,95}", candidate):
                    reason = candidate
                else:
                    reason = "provider_capability_refused"
            # An ImportError may name the missing module or an absent
            # imported symbol. The provider's complete exception text is not
            # a stable diagnostic field and may include signed URLs. Capture
            # only syntactically valid Python names from the exception itself
            # and the fixed CPython "cannot import name" prefix.
            import_details = ""
            if isinstance(error, ImportError):
                raw_name = error.name
                import_name = (
                    raw_name if type(raw_name) is str
                    and len(raw_name) <= 255
                    and re.fullmatch(
                        r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*",
                        raw_name,
                    )
                    else "unknown"
                )
                import_symbol = "unknown"
                if type(error.msg) is str:
                    match = re.match(
                        r"cannot import name '([A-Za-z_][A-Za-z0-9_]{0,127})' "
                        r"from (?:partially initialized module )?"
                        r"'([A-Za-z_][A-Za-z0-9_.]{0,255})'",
                        error.msg,
                    )
                    if match:
                        imported_from = match.group(2)
                        if (import_name == "unknown" and re.fullmatch(
                            r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*",
                            imported_from,
                        )):
                            import_name = imported_from
                        import_symbol = match.group(1)
                import_details = (
                    f" import_name={import_name} import_symbol={import_symbol}"
                )
            _LOG.info(
                "EVENT canonical selected_offer provider_id=%s product_id=%s "
                "outcome=unknown phase=provider_preflight reason=%s "
                "exception_type=%s%s",
                scope.provider_id, key.right_id, reason, type(error).__name__,
                import_details,
            )
            continue
        if cancelled():
            raise Cancelled("canonical_offer_preflight_cancelled")
        if not current():
            raise ValueError("canonical_offer_source_rebound")
        if not consent_current(key):
            continue
        if reader_name == "downloadable_selected_offer_snapshot":
            if type(selected) is not ResourceSnapshot:
                continue
            try:
                selected = as_legacy_rights_snapshot(selected)
            except (AttributeError, TypeError, ValueError):
                # A malformed v2 selected claim is not an admitted offer.
                # The reader has already returned: reject only this resource,
                # with no legacy fallback, without aborting unrelated A rows.
                _LOG.warning("TRACE selected offer code=canonical_selected_v2_invalid")
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
