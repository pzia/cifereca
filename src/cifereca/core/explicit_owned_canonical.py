"""#5054 — one explicit owned Library click through the canonical A→B executor.

No source-wide ON is written. The already selected owned possession is
revalidated under the global lock; the producer selects one exact offer.
Neither legacy download_direct nor a provider-specific receipt writer
is reachable on this path.
"""
from __future__ import annotations

from inspect import getattr_static
from pathlib import Path

from ..acquisition import Acquisition
from ..locking import InterProcessLock
from ..models import Account
from ..paths import acquisition_lock_file
from ..plugin_api.downloadable_resources import (
    as_legacy_rights_snapshot,
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
    from .selected_source_offer import SourceOfferTarget
    # The retired entitlement-backed target is no longer executable.
    if (type(target) is not SourceOfferTarget or target.entitlement_id is not None
            or not callable(resolve) or not callable(right_current)
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
                    and (target.reader_scope is None
                         or target.reader_scope.current(service))
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
            from .explicit_selected_offer import exact_selected_offer_id
            offer_id = exact_selected_offer_id(observed, key)
            if offer_id is None:
                raise ValueError("explicit_canonical_offer_unattested")
            selected_offer_id[0] = offer_id
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
                # The publisher independently verifies the durable stage
                # for this exact selected offer inside the same transaction.
                return selected_offer_id[0] is not None

            result = execute_admitted_convergence(
                service, account, session, snapshot,
                admitted_epoch=epoch, shared_lock=lease,
                library_root=Path(service.library_dir()),
                persisted_consent=frozenset({key}),
                consent_still_current=live_permission,
                explicitly_confirmed_once=True,
                explicit_offer_id=offer_id,
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
                    }):
                raise ValueError("explicit_canonical_delivery_unconfirmed")
            # Slots on a legacy Placement no longer carry provider or edition.
            # Resolve the exact current source via its independent OriginLink
            # and newly verified physical B, under the same acquisition lease.
            # A successful commit is never a reason to retry the supplier GET.
            from .convergence_verified_presence import plan_existing_verified_presence
            fresh = plan_existing_verified_presence(
                storage, snapshot, library_root=Path(service.library_dir()),
            )
            if (len(fresh.decisions) != 1 or fresh.decisions[0].source != key
                    or not fresh.decisions[0].provider_satisfied
                    or not live_permission(key)):
                raise ValueError("explicit_canonical_placement_unavailable")
            outcome = result[0]
            published_id = outcome.placement_id
            ids = fresh.decisions[0].placement_ids
            if published_id:
                if published_id not in ids or not outcome.artifact_id:
                    raise ValueError("explicit_canonical_placement_unavailable")
                ids = (published_id,)
            placements = []
            for pid in ids:
                row = storage.placement_get(pid)
                if (type(row) is not dict or row.get("placement_id") != pid
                        or row.get("identity_id") != target.identity_id
                        or row.get("state") != "present"
                        or type(row.get("path")) is not str
                        or type(row.get("artifact_id")) is not str):
                    raise ValueError("explicit_canonical_placement_unavailable")
                if ".canonical-delivery" in Path(row["path"]).parts:
                    continue
                if not outcome.artifact_id or row["artifact_id"] == outcome.artifact_id:
                    placements.append(row)
            # Multiple attested byte-identical copies in this same profile are
            # equivalent for display only. Different digests remain ambiguous.
            # No selection, deduplication or provider authority is derived
            # from an arbitrary folder or filename.
            if not placements or len({row["artifact_id"] for row in placements}) != 1:
                raise ValueError("explicit_canonical_placement_ambiguous")
            placement = min(placements, key=lambda row: row["placement_id"])
            artifact_id = placement["artifact_id"]
            path = service.local_book_path(
                placement["placement_id"], identity_id=target.identity_id,
            )
            if (not isinstance(path, Path) or not path.is_file()
                    or storage.placement_get(placement["placement_id"]) != placement
                    or not live_permission(key)):
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
