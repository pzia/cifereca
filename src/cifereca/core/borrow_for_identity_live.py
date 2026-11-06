"""#4662: bind observed borrow opportunities to *live* Core lender sessions.

This read-only producer accepts previously collected, exact borrower evidence.
It never discovers a lender, authenticates, borrows, transfers, refreshes a
source or treats a catalogue's CanLoan as a personal permission. The returned
options are non-executable; a later explicit action must revalidate everything.
"""
from __future__ import annotations

from dataclasses import dataclass
from time import time

from ..models import Account, SearchResult
from ..plugin_api.lending_capabilities import (
    BorrowerOperationEvidence, assess_borrower_operation_eligibility,
    assess_lending_operation,
)
from ..plugin_api.lending_borrower_eligibility import (
    BorrowerOperationEvidence as ExactEditionBorrowerEvidence,
    assess_borrower_eligibility,
)
from ..providers.base import ProviderDescriptor
from .borrow_action_scope import borrow_claim_key
from .borrow_for_identity_preview import (
    BorrowForIdentityPreview, BorrowForIdentityWitness,
    _display_account_label, preview_borrow_for_identity,
)
from .loan_action_scope import _inert_session_accounts, _inert_session_state
from .manual_paris_borrow import _normal_paris_source_current
from .source_context import source_context_epoch


@dataclass(frozen=True, slots=True, repr=False)
class ObservedBorrowOffer:
    """An already-observed candidate; evidence is not a permission token.

    The producer must have independently observed the borrower operation for
    precisely this branch. A bare SearchResult is never sufficient.
    """

    notice: SearchResult
    evidence: BorrowerOperationEvidence | None
    edition_id: str
    observed_at: int
    # Separate, independently observed permission for this exact source
    # collection and record_id. Account-wide loan capability/permission is
    # never proof that a particular digital edition may be borrowed.
    # Legacy callers without this fact must see zero options, not an error.
    edition_evidence: ExactEditionBorrowerEvidence | None = None
    # A provider loan does not imply any transferable use/copy entitlement.
    transfer_state: str = "unknown"


def _lender_label(value: object) -> str:
    # A profile label is user-configurable and may itself contain a login.
    label = _display_account_label(value)
    return "Profil prêteur" if label == "Compte bibliothèque" else label


def _observation_route(row: ObservedBorrowOffer) -> tuple[str, ...] | None:
    """Local identity/account/edition scope for contradictory observations.

    Missing or foreign borrower identities cannot veto unrelated lenders.
    The route deliberately excludes catalogue row IDs, display labels,
    timestamps and positive/negative status, none of which identify a loan.
    """
    item, evidence = row.notice, row.evidence
    if (type(item) is not SearchResult
            or type(evidence) is not BorrowerOperationEvidence
            or type(row.edition_id) is not str):
        return None
    coords = (
        evidence.identity_id, evidence.provider_id,
        evidence.credential_id, evidence.account_id,
        item.docbase, item.record_id, item.source_collection or "",
        row.edition_id,
    )
    if (any(type(x) is not str or len(x) > 1024 or "\\x00" in x
            for x in coords)
            or not all(coords[i] for i in (0, 1, 2, 4, 5, 7))
            or (item.source_provider, item.source_credential,
                item.docbase) != (
                    evidence.provider_id, evidence.credential_id,
                    evidence.branch,
                )):
        return None
    return coords


def _noncontradictory_observations(
    observations: tuple[ObservedBorrowOffer, ...] | list[ObservedBorrowOffer],
) -> tuple[ObservedBorrowOffer, ...]:
    """Do not let an old positive survive a denial for the exact same route.

    Only same-identity/credential/borrower/book/collection candidates
    conflict. A separate lender or edition stays independently selectable.
    Duplicate observations with identical facts are harmless; distinct
    freshness, permissions, delivery policy or medium are ambiguous.
    """
    seen: dict[tuple[str, ...], ObservedBorrowOffer] = {}
    contradictions: set[tuple[str, ...]] = set()
    for row in observations:
        route = _observation_route(row)
        if route is None or route in contradictions:
            continue
        previous = seen.get(route)
        if previous is None:
            seen[route] = row
            continue
        if ((previous.evidence, previous.edition_evidence,
             previous.observed_at, previous.transfer_state,
             previous.notice.medium) !=
            (row.evidence, row.edition_evidence, row.observed_at,
             row.transfer_state, row.notice.medium)):
            contradictions.add(route)
            del seen[route]
    return tuple(seen.values())


def _witness(service, row: ObservedBorrowOffer, *, context_epoch: int,
             source_epoch: int) -> BorrowForIdentityWitness | None:
    """Rebind every coordinate to in-memory Core and owned SQLite state.

    Never invoke session.accounts, source getters, provider operations, or
    remote eligibility checks. An unknown fact is a rejected preview option.
    """
    item, evidence = row.notice, row.evidence
    if (type(item) is not SearchResult
            or type(evidence) is not BorrowerOperationEvidence
            or type(row.edition_evidence) is not ExactEditionBorrowerEvidence
            or type(row.edition_id) is not str
            or type(row.observed_at) is not int
            or row.transfer_state not in ("unknown", "blocked")
            or type(evidence.identity_id) is not str
            or type(evidence.provider_id) is not str
            or type(evidence.credential_id) is not str
            or type(evidence.account_id) is not str
            or evidence.operation != "loan.borrow"
            or (evidence.provider_id, evidence.credential_id,
                evidence.branch) != (
                    item.source_provider, item.source_credential, item.docbase,
                )
            or item.medium != "digital"):
        return None

    try:
        # All cross-profile lenders must already be known locally; never
        # use the active reader's session as a substitute.
        if (service.identity_by_id(evidence.identity_id) is None
                or service.storage.identity_for_connection(
                    evidence.provider_id, evidence.credential_id,
                ) != evidence.identity_id):
            return None
        connections = service.storage.provider_connections(
            identity_id=evidence.identity_id,
        )
        if (type(connections) is not list or len(connections) > 512
                or sum(
                    type(conn) is dict
                    and conn.get("provider_id") == evidence.provider_id
                    and conn.get("credential_id") == evidence.credential_id
                    and conn.get("identity_id") == evidence.identity_id
                    and conn.get("enabled") is True
                    for conn in connections
                ) != 1):
            return None

        accounts = service.accounts
        if type(accounts) not in (list, tuple) or len(accounts) > 512:
            return None
        matches = [
            account for account in accounts
            if type(account) is Account and account.is_primary is True
            and (account.identity_id, account.provider_id,
                 account.credential_id, account.unique_id) == (
                     evidence.identity_id, evidence.provider_id,
                     evidence.credential_id, evidence.account_id,
                 )
        ]
        if len(matches) != 1:
            return None
        account = matches[0]
        session_key = service._session_key(
            evidence.provider_id, evidence.credential_id,
        )
        session = service._sessions.get(session_key)
        state = _inert_session_state(session)
        rows = _inert_session_accounts(session)
        if (state is None or rows is None
                or state.get("connected") is not True
                or state.get("_closed") is True
                or state.get("provider_id") != evidence.provider_id
                or state.get("credential_id") != evidence.credential_id
                or sum(other is account for other in rows) != 1):
            return None

        # Paris's normal borrow contract supports exactly the selected
        # primary. Other profiles are NEVER activated to inspect this fact.
        if (evidence.provider_id == "paris"
                and not _normal_paris_source_current(
                    service, item, evidence.identity_id,
                )):
            return None

        plugin_id = service._provider_plugin_ids.get(evidence.provider_id)
        manager = service._plugin_manager
        if type(plugin_id) is not str or not plugin_id or manager is None:
            return None
        plugin_status = manager.status(plugin_id)
        declaration = plugin_status.descriptor
        if (plugin_status.enabled is not True
                or plugin_status.state != "ready"
                or declaration.id != plugin_id
                or declaration.type != "provider.library"):
            return None
        enabled_for = getattr(manager, "enabled_for_identity", None)
        if callable(enabled_for) and enabled_for(
                plugin_id, evidence.identity_id) is not True:
            return None
        descriptor = state.get("descriptor")
        if (type(descriptor) is not ProviderDescriptor
                or descriptor.id != evidence.provider_id):
            return None
        capability = assess_lending_operation(
            "loan.borrow",
            manifest_capabilities=declaration.capabilities,
            descriptor=descriptor, session=session, account=account,
        )
        eligibility = assess_borrower_operation_eligibility(
            capability, account=account, branch=item.docbase,
            evidence=evidence,
        )
        if eligibility.state != "eligible_observed":
            return None
        # #4662: borrower-wide readiness can be true while *this book*
        # is unavailable. Never qualify a different edition/collection,
        # a catalogue-only CanLoan hint, or a generic branch permission.
        exact_offer = assess_borrower_eligibility(
            capability=capability, account=account, notice=item,
            evidence=row.edition_evidence,
        )
        if exact_offer.status != "eligible":
            return None
        key = borrow_claim_key(account, item)
        if key is None or service.storage.metadata_get(key) is not None:
            return None

        # The context/source generation must be stable throughout reads.
        # This avoids publishing even a non-executable stale option after
        # an ABA switch or a credential/session replacement.
        session_epoch = state.get("_epoch", source_epoch)
        if type(session_epoch) is not int or session_epoch < 0:
            return None
        # A reconnected session may reuse an identical local _epoch and
        # credential. Include the Core's independent source-generation in
        # the option ID: Cantor pairing is injective for nonnegative ints,
        # unlike addition, and never exposes the two underlying counters.
        pinned_epoch = (
            (source_epoch + session_epoch) *
            (source_epoch + session_epoch + 1) // 2 + session_epoch
        )
        if (service._sessions.get(session_key) is not session
                or _inert_session_state(session) is not state
                or state.get("connected") is not True
                or state.get("_epoch", source_epoch) != session_epoch
                or service.storage.identity_for_connection(
                    evidence.provider_id, evidence.credential_id,
                ) != evidence.identity_id):
            return None
        lender = service.identity_by_id(evidence.identity_id)
        name = _lender_label(getattr(lender, "label", None))
        return BorrowForIdentityWitness(
            account=account, notice=item, capability=capability,
            eligibility=eligibility, lender_name=name,
            observed_at=row.observed_at, service_context_epoch=context_epoch,
            source_epoch=pinned_epoch, edition_id=row.edition_id,
            transfer_state=row.transfer_state,
        )
    except Exception:
        # A failed local fact lookup is UNKNOWN, never a reason to offer an
        # action or to expose the provider exception in UI/diagnostics.
        return None


def preview_live_borrow_for_current_identity(
    service, *, expected_edition_id: str,
    observations: object, now: int | None = None,
) -> BorrowForIdentityPreview:
    """Project live Core owner/session facts without switching identities.

    This is a UI read model. It is deliberately not an executor: even a
    positive borrower observation is not current supplier permission or a
    license for copying to another identity.
    """
    if now is None:
        now = int(time())
    if (type(observations) not in (tuple, list)
            or len(observations) > 64
            or any(type(row) is not ObservedBorrowOffer
                   for row in observations)):
        raise ValueError("invalid_cross_profile_borrow_observations")

    identity = service.current_identity
    requesting = getattr(identity, "identity_id", None)
    context = getattr(service, "_account_context_epoch", None)
    if type(context) is not int or context < 0:
        raise ValueError("invalid_cross_profile_borrow_context")
    source = source_context_epoch(service)
    # A newer denial or unknown status for one exact borrower/edition must
    # not be discarded while retaining an older positive witness. Refuse
    # contradictory duplicates before converting them to admissible options.
    scoped = _noncontradictory_observations(observations)
    witnesses = tuple(
        witness for row in scoped
        if (witness := _witness(
            service, row, context_epoch=context, source_epoch=source,
        )) is not None
    )
    # The pure preview validates all user-provided edition/time identifiers.
    result = preview_borrow_for_identity(
        requesting_identity_id=requesting,
        expected_edition_id=expected_edition_id,
        witnesses=witnesses, now=now, context_epoch=context,
    )
    if (service.current_identity is not identity
            or getattr(service, "_account_context_epoch", None) != context
            or source_context_epoch(service) != source):
        return preview_borrow_for_identity(
            requesting_identity_id=requesting,
            expected_edition_id=expected_edition_id,
            witnesses=(), now=now, context_epoch=context,
        )
    return result


@dataclass(frozen=True, slots=True)
class BorrowLenderSelection:
    """Non-authorizing proof that an explicit UI choice still matches Core.

    It contains neither a borrower credential nor permission to mutate. A
    separate consent/loan executor must obtain a fresh, durable action lease.
    """

    status: str  # current | revoked
    reason_code: str  # selection_current_unverified | selection_stale
    borrowing_identity_id: str | None = None
    label: str | None = None
    transfer_state: str = "unknown"
    executable: bool = False


def revalidate_live_borrow_lender_selection(
    service, *, captured_preview: BorrowForIdentityPreview,
    selected_option_id: str, expected_edition_id: str,
    observations: object, now: int | None = None,
) -> BorrowLenderSelection:
    """Re-evaluate the *clicked* option, never pick the first eligible offer.

    A previously opened GTK detail/menu can retain a valid-looking option_id
    after an ABA identity switch or a source rebind. It must be rejected even
    if the title, account label and notice ID look identical. This is only a
    read-only click preflight, not the manual borrowing transaction.
    """
    stale = BorrowLenderSelection("revoked", "selection_stale")
    if (type(captured_preview) is not BorrowForIdentityPreview
            or captured_preview.executable is not False
            or type(selected_option_id) is not str
            or not selected_option_id.startswith("borrow-profile.v1:")
            or type(expected_edition_id) is not str
            or type(captured_preview.options) is not tuple):
        return stale
    selected = [
        option for option in captured_preview.options
        if option.option_id == selected_option_id
        and option.executable is False
        and option.edition_id == expected_edition_id
    ]
    if len(selected) != 1:
        return stale
    try:
        fresh = preview_live_borrow_for_current_identity(
            service, expected_edition_id=expected_edition_id,
            observations=observations, now=now,
        )
        if (fresh.requesting_identity_id != captured_preview.requesting_identity_id
                or fresh.executable is not False
                or fresh.status != captured_preview.status
                or fresh.requires_explicit_choice !=
                    captured_preview.requires_explicit_choice):
            return stale
        matching = [
            option for option in fresh.options
            if option.option_id == selected_option_id
        ]
        # Compare the *whole* current option: a renamed account, changed
        # edition, source, transfer restriction or claim is not the old click.
        if len(matching) != 1 or matching[0] != selected[0]:
            return stale
        option = matching[0]
        return BorrowLenderSelection(
            "current", "selection_current_unverified",
            borrowing_identity_id=option.borrowing_identity_id,
            label=option.label, transfer_state=option.transfer_state,
        )
    except Exception:
        # Malformed or stale evidence never falls back to another lender,
        # and must not emit provider errors or account IDs to GTK.
        return stale


__all__ = [
    "ObservedBorrowOffer", "BorrowLenderSelection",
    "preview_live_borrow_for_current_identity",
    "revalidate_live_borrow_lender_selection",
]
