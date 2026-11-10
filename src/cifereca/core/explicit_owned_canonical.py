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


def obtain_explicit_owned(service, target, *, resolve) -> MaterializationResult:
    """Exact, one-shot Core permission; never implicit source-wide Sync consent."""
    if (not callable(resolve) or type(getattr(target, "provider_id", None)) is not str
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
                fresh, _ = resolve(
                    service, target.entitlement_id,
                    target.identity_id, target.item_id,
                )
                return fresh == target and fresh.session is session
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
            if not live_permission(key):
                raise ValueError("explicit_canonical_permission_revoked")
            snapshot = as_legacy_rights_snapshot(observed)
            persist_attested_source_references(
                storage, snapshot, context_current=context_current,
            )
            if not live_permission(key):
                raise ValueError("explicit_canonical_permission_revoked")
            result = execute_admitted_convergence(
                service, account, session, snapshot,
                admitted_epoch=epoch, shared_lock=lease,
                library_root=Path(service.library_dir()),
                persisted_consent=frozenset({key}),
                consent_still_current=live_permission,
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
                acquired, {"status": "indexed", "artifact_id": artifact_id},
                artifact_id, target.identity_id,
            )
        finally:
            # A retained callback is never a perpetual acquisition grant.
            live[0] = False
