"""#5054 — one explicit owned Library click through the canonical A→B executor.

No source-wide ON is written. The already selected owned possession is
revalidated under the global lock; the producer selects one exact offer.
Neither legacy download_direct nor a provider-specific receipt writer
is reachable on this path.
"""
from __future__ import annotations

from inspect import getattr_static
import json
from pathlib import Path

from ..acquisition import Acquisition
from ..locking import InterProcessLock
from ..models import Account
from ..paths import acquisition_lock_file
from ..plugin_api.downloadable_resources import (
    ResourceSnapshot, as_legacy_rights_snapshot,
)
from ..plugin_api.provider_rights_observation import ProviderRightsScope
from .canonical_delivery_abi import has_complete_delivery_abi
from .canonical_selected_delivery import complete_selected_delivery
from .canonical_sync_stage import persist_attested_source_references
from .convergence_execution import execute_admitted_convergence
from .materialization import MaterializationResult
from .source_context import source_context_epoch
from .source_convergence import SourceKey


def obtain_explicit_owned(service, target, *, resolve, right_current) -> MaterializationResult:
    """Exact, one-shot Core permission; never implicit source-wide Sync consent."""
    if (not callable(resolve) or not callable(right_current)
            or type(getattr(target, "provider_id", None)) is not str
            or not target.provider_id or type(getattr(target, "branch", None)) is not str
            or not target.branch):
        raise ValueError("explicit_canonical_target_invalid")

    with InterProcessLock(acquisition_lock_file()) as lease:
        pinned, item = resolve(
            service, target.entitlement_id, target.identity_id, target.item_id,
        )
        if pinned != target or pinned.session is not target.session:
            raise ValueError("explicit_canonical_target_changed")
        session = target.session
        if not has_complete_delivery_abi(session):
            raise ValueError("explicit_canonical_abi_unavailable")
        selected = getattr_static(session, "downloadable_selected_offer_snapshot", None)
        guarded = getattr_static(
            session, "downloadable_selected_delivery_candidate_guarded", None,
        )
        # This manual cutover initially supports the fully guarded v2 producer
        # only. A v1 or unguarded v2 producer is NOT a fallback permission.
        if not callable(selected) or not callable(guarded):
            raise ValueError("explicit_canonical_guarded_abi_required")
        accounts = [
            account for account in service.accounts
            if type(account) is Account
            and account.identity_id == target.identity_id
            and account.provider_id == target.provider_id
            and account.credential_id == target.credential_id
            and account.unique_id == target.account_id
        ]
        if len(accounts) != 1:
            raise ValueError("explicit_canonical_account_ambiguous")
        account = accounts[0]
        epoch = source_context_epoch(service)
        registry = service.accounts
        sessions = service._sessions
        storage = service.storage
        scope = ProviderRightsScope(
            target.identity_id, target.provider_id, target.credential_id, target.account_id,
        )
        key = SourceKey(scope, target.item_id, target.item_id, "purchase", target.branch)
        live = [True]
        selected_offer_id = [None]

        def context_current() -> bool:
            """Pure in-memory guard: safe in Core's BEGIN IMMEDIATE."""
            try:
                return (
                    live[0]
                    and source_context_epoch(service) == epoch
                    and service.accounts is registry
                    and service._sessions is sessions
                    and service.storage is storage
                    and service._sessions.get(
                        service._session_key(target.provider_id, target.credential_id)
                    ) is session
                    and session.connected is True
                    and getattr(session, "_closed", False) is False
                    and session.provider_id == scope.provider_id
                    and session.credential_id == scope.credential_id
                    and isinstance(session.accounts, (list, tuple))
                    and sum(
                        type(candidate) is Account
                        and (candidate.identity_id, candidate.provider_id,
                             candidate.credential_id, candidate.unique_id)
                        == (scope.identity_id, scope.provider_id,
                            scope.credential_id, scope.account_id)
                        for candidate in session.accounts
                    ) == 1
                    and sum(a is account for a in registry) == 1
                    and (account.identity_id, account.provider_id,
                         account.credential_id, account.unique_id)
                    == (scope.identity_id, scope.provider_id,
                        scope.credential_id, scope.account_id)
                )
            except Exception:
                return False

        def live_permission(source: SourceKey) -> bool:
            if source != key or not context_current():
                return False
            try:
                # The exact target was admitted by resolve() before any GET.
                # This separate right-only predicate stays valid after Core
                # writes its own durable pending receipt. Re-running resolve()
                # after that receipt would reject its own successful GET as a
                # competing acquisition and prevent canonical publication.
                return right_current(
                    service, target, selected_offer_id=selected_offer_id[0],
                ) is True
            except Exception:
                return False

        try:
            if not live_permission(key):
                raise ValueError("explicit_canonical_permission_revoked")
            # Exactly one source-specific private offer preflight. No catalogue
            # flag, stored URL or historical receipt can supply these bytes.
            observed = session.downloadable_selected_offer_snapshot(
                account, edition_id=target.item_id, resource_id=target.item_id,
                kind="purchase", branch=target.branch,
                explicit_user_action=True,
            )
            if (type(observed) is not ResourceSnapshot
                    or observed.scope != scope or observed.branch != key.branch
                    or observed.status not in {"success", "partial"}
                    or len(observed.resources) != 1):
                raise ValueError("explicit_canonical_offer_unattested")
            offer = observed.resources[0]
            if (offer.source.scope != scope
                    or offer.source.branch != key.branch
                    or (offer.source.edition_id, offer.source.resource_id,
                        offer.source.kind) != (
                        key.edition_id, key.right_id, key.kind)
                    or not offer.can_propose_delivery):
                raise ValueError("explicit_canonical_offer_mismatch")
            selected_offer_id[0] = offer.capability.offer_id
            if not live_permission(key):
                raise ValueError("explicit_canonical_permission_revoked")
            # The strict target resolver (including no competing receipt)
            # is replayed just before Core A is written. Only the explicit
            # right-only lease is used *after* a canonical receipt exists.
            refreshed, _ = resolve(
                service, target.entitlement_id,
                target.identity_id, target.item_id,
            )
            if refreshed != target or refreshed.session is not session:
                raise ValueError("explicit_canonical_target_changed")
            snapshot = as_legacy_rights_snapshot(observed)
            persist_attested_source_references(
                storage, snapshot, context_current=context_current,
            )
            if not live_permission(key):
                raise ValueError("explicit_canonical_permission_revoked")
            def publication_guard(db, source: SourceKey) -> bool:
                """One exact positive right under publisher's BEGIN IMMEDIATE.

                No Storage call, remote GET, plugin callback or source-wide
                consent write occurs while the SQL transaction is held.
                The click itself is the ephemeral right-level permission;
                all borrower/credential and final bytes checks remain Core's.
                """
                if source != key or not context_current():
                    return False
                try:
                    rows = db.execute(
                        "SELECT entitlement_type,state,loan_id,expires_at,"
                        "metadata_json FROM entitlements "
                        "WHERE entitlement_id=? AND identity_id=? "
                        "AND provider_id=? AND credential_id=? "
                        "AND account_id=? AND item_id=? LIMIT 2",
                        (target.entitlement_id, target.identity_id,
                         target.provider_id, target.credential_id,
                         target.account_id, target.item_id),
                    ).fetchall()
                    if len(rows) != 1:
                        return False
                    kind, state, loan, expiry, metadata_json = rows[0]
                    if (kind != "owned" or state != "active"
                            or loan is not None or expiry is not None):
                        return False
                    metadata = json.loads(metadata_json)
                    return (
                        type(metadata) is dict
                        and metadata.get("source") == target.attested_source
                        and metadata.get("downloadable") is not False
                        and metadata.get("owned") is not False
                        and metadata.get("archived") is not True
                        and all(
                            metadata.get(name) is None
                            or type(metadata[name]) is bool
                            for name in ("downloadable", "owned", "archived")
                        )
                    )
                except Exception:
                    return False

            result = execute_admitted_convergence(
                service, account, session, snapshot,
                admitted_epoch=epoch, shared_lock=lease,
                library_root=Path(service.library_dir()),
                persisted_consent=frozenset({key}),
                consent_still_current=live_permission,
                explicitly_confirmed_once=True,
                explicit_entitlement_id=target.entitlement_id,
                publication_guard=publication_guard,
                deliver=lambda source, chosen: complete_selected_delivery(
                    service, account, session, source, chosen,
                    library_root=Path(service.library_dir()),
                    shared_lock=lease, consent_still_current=(
                        lambda: live_permission(source)
                    ),
                ),
            )
            if (len(result) != 1 or result[0].source != key
                    or result[0].status not in {
                        "downloaded", "recovered", "present_attested",
                    }
                    or not result[0].artifact_id):
                raise ValueError("explicit_canonical_delivery_unconfirmed")
            artifact_id = result[0].artifact_id
            placements = [
                row for row in storage.placements()
                if row.get("identity_id") == target.identity_id
                and row.get("provider_id") == target.provider_id
                and row.get("artifact_id") == artifact_id
                and row.get("state") == "present"
                and row.get("source_item_id") == target.item_id
            ]
            if len(placements) != 1:
                raise ValueError("explicit_canonical_placement_ambiguous")
            path = service.local_book_path(
                placements[0]["placement_id"], identity_id=target.identity_id,
            )
            if not isinstance(path, Path) or not path.is_file():
                raise ValueError("explicit_canonical_placement_unavailable")
            acquired = Acquisition(
                provider_id=target.provider_id, credential_id=target.credential_id,
                account_id=target.account_id, item_id=target.item_id,
                loan_id=None, kind="already_local", source_url=None,
                local_path=path, artifact_path=path, target_dir=path.parent,
                mime_type=None, drm="unknown",
                metadata={"identity_id": target.identity_id},
            )
            return MaterializationResult(
                # The canonical publisher attests B; its optional catalogue
                # projection is advisory. Never fabricate an indexed receipt.
                acquired, None, artifact_id, target.identity_id,
            )
        finally:
            # A retained callback is never a perpetual acquisition grant.
            live[0] = False
