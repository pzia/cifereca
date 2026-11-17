"""Provider-neutral, exact source offer selection for explicit manual delivery.

No entitlement, ownership history, provider URL or pseudo-right is read here.
Only the real, bound supplier session can attest a current selected offer.
The canonical Core executor independently verifies staging, SHA and placement.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from inspect import getattr_static

from ..models import Account
from .canonical_delivery_abi import has_complete_delivery_abi
from .private_inventory_binding import exact_private_inventory_binding_valid


@dataclass(frozen=True, repr=False)
class SourceOfferTarget:
    identity_id: str
    provider_id: str
    credential_id: str
    account_id: str
    item_id: str
    branch: str
    session: object = field(compare=False, repr=False)
    entitlement_id: None = None
    # Pinned by the synchronous Core facade. A→B→A can restore the same
    # identity string but cannot restore the account-context generation.
    selection_epoch: int | None = field(default=None, compare=False)
    # Only an explicit Core-admitted Search click may use this independent
    # source lease. Passive/legacy targets retain navigation-epoch semantics.
    source_epoch: int | None = field(default=None, compare=False)


def _resolve(service, target: SourceOfferTarget):
    """Recheck the exact account and live delivery ABI without provider I/O."""
    if type(target) is not SourceOfferTarget:
        raise ValueError("selected_source_target_invalid")
    # Execution is headless: compare only the captured context generation.
    # Production LibraryService publishes a monotone epoch on every identity
    # switch; minimal synthetic facades with no epoch still need all the
    # independent account/session/ABI binding guards below.
    if target.source_epoch is not None:
        from .source_context import source_context_epoch
        if (type(target.source_epoch) is not int
                or target.source_epoch < 0
                or target.selection_epoch is not None
                or source_context_epoch(service) != target.source_epoch):
            raise ValueError("selected_source_binding_changed")
    else:
        # Historical/read-only selection targets remain navigation-scoped.
        current_epoch = getattr(service, "_account_context_epoch", None)
        if (type(current_epoch) not in (int, type(None))
                or (type(current_epoch) is int and current_epoch < 0)
                or target.selection_epoch != current_epoch):
            raise ValueError("selected_source_identity_changed")
    accounts = [
        account for account in service.accounts
        if type(account) is Account
        and (account.identity_id, account.provider_id, account.credential_id,
             account.unique_id) == (
                 target.identity_id, target.provider_id,
                 target.credential_id, target.account_id,
             )
    ]
    if len(accounts) != 1:
        raise ValueError("selected_source_account_ambiguous")
    account = accounts[0]
    session = service._sessions.get(
        service._session_key(target.provider_id, target.credential_id)
    )
    if (session is not target.session
            or getattr_static(session, "selected_purchase_branch", None) != target.branch
            or not has_complete_delivery_abi(session)
            or not callable(getattr_static(
                session, "downloadable_selected_delivery_candidate_guarded", None
            ))
            or not exact_private_inventory_binding_valid(
                service, account, session=session, provider_id=target.provider_id,
            )):
        raise ValueError("selected_source_binding_changed")
    if target.source_epoch is not None:
        from .source_context import source_context_epoch
        if source_context_epoch(service) != target.source_epoch:
            raise ValueError("selected_source_binding_changed")
    return target, account


def select_source_offer_target(
    service, *, provider_id: str, credential_id: str, item_id: str,
    identity_id: str | None = None,
    expected_epoch: int | None = None,
):
    """Passive target construction: never performs a catalogue or content GET."""
    if (not all(type(x) is str and x and len(x) <= 1024
                and not any(c in x for c in "\x00\r\n")
                for x in (provider_id, credential_id, item_id, identity_id))):
        return None
    try:
        identity = identity_id
        epoch = getattr(service, "_account_context_epoch", None)
        if (type(epoch) not in (int, type(None))
                or (type(epoch) is int and epoch < 0)
                or (expected_epoch is not None and expected_epoch != epoch)):
            return None
        accounts = [a for a in service.accounts
                    if a.provider_id == provider_id
                    and a.credential_id == credential_id
                    and a.identity_id == identity]
        if len(accounts) != 1:
            return None
        account = accounts[0]
        session = service._sessions.get(service._session_key(provider_id, credential_id))
        branch = getattr_static(session, "selected_purchase_branch", None)
        if type(branch) is not str or not branch or len(branch) > 128:
            return None
        target = SourceOfferTarget(
            identity, provider_id, credential_id, account.unique_id,
            item_id, branch, session, selection_epoch=epoch,
        )
        _resolve(service, target)
        return target
    except Exception:
        return None


def obtain_source_offer(service, target: SourceOfferTarget):
    """Core's existing global-lock, single-offer A→B path, without entitlements."""
    def resolve(_service, entitlement_id, identity_id, item_id):
        if (entitlement_id is not None or identity_id != target.identity_id
                or item_id != target.item_id):
            raise ValueError("selected_source_coordinates_changed")
        pinned, _ = _resolve(_service, target)
        return pinned, None

    def current(_service, selected, *, selected_offer_id=None):
        if selected is not target:
            return False
        try:
            _resolve(_service, target)
            # Admission itself never depends on a stored offer: the provider's
            # fresh selected snapshot is checked independently under lock.
            return True
        except Exception:
            return False

    from .explicit_owned_canonical import obtain_explicit_owned
    return obtain_explicit_owned(
        service, target, resolve=resolve, right_current=current,
    )


def select_library_source_offer_target(
    service, entry, *, identity_id: str | None = None,
    expected_epoch: int | None = None,
):
    """Select exactly one remote origin from a Library card; never a right.

    Historical possession rows may still carry a legacy entitlement ID. That
    ID is intentionally ignored; it confers neither offer nor download access.
    The provider must attest a current selected offer after the click.
    """
    if (type(entry) is not dict or entry.get("local")
            or entry.get("local_path") or entry.get("placement_id")):
        return None
    identity = identity_id
    if (type(identity) is not str or not identity or len(identity) > 1024
            or any(c in identity for c in "\x00\r\n")):
        return None
    native_origin = entry.get("source") == "private_catalog_observation"
    if native_origin:
        # A private catalogue card has its own exact source. Historical
        # possessions are not a source selector, fallback or competing
        # authority for this card.
        refs = entry.get("source_refs")
        if (entry.get("state") != "remote_catalog"
                or type(refs) not in (tuple, list) or len(refs) != 1
                or type(refs[0]) is not dict):
            return None
        candidate = refs[0]
        if (candidate.get("identity_id") != identity
                or candidate.get("kind") != "purchase"):
            return None
        credential = candidate.get("credential_id")
        if (type(credential) is not str or not credential
                or len(credential) > 1024
                or any(c in credential for c in "\x00\r\n")):
            return None
    else:
        # Historical possession-only cards still supply display/navigation
        # coordinates until their own private-inventory observation exists.
        # They cannot grant an offer or publish bytes.
        candidates = [
            row for row in (entry.get("possessions") or ())
            if type(row) is dict and row.get("identity_id") == identity
            and row.get("kind") == "owned" and row.get("state") == "active"
        ] if type(entry.get("possessions")) in (tuple, list) else []
        if len(candidates) != 1:
            return None
        candidate = candidates[0]
        credential = None
    provider = candidate.get("provider_id")
    item_id = candidate.get("item_id")
    account_id = candidate.get("account_id")
    if (not all(type(value) is str and value and len(value) <= 1024
                and not any(c in value for c in "\x00\r\n")
                for value in (provider, item_id))
            or type(account_id) is not str or len(account_id) > 1024
            or any(c in account_id for c in "\x00\r\n")):
        return None
    accounts = [
        account for account in service.accounts
        if type(account) is Account
        and account.identity_id == identity
        and account.provider_id == provider
        and account.unique_id == account_id
        and (not native_origin or account.credential_id == credential)
    ]
    if len(accounts) != 1:
        return None
    pinned = {"expected_epoch": expected_epoch} if expected_epoch is not None else {}
    return select_source_offer_target(
        service, provider_id=provider, credential_id=accounts[0].credential_id,
        item_id=item_id, identity_id=identity, **pinned,
    )
