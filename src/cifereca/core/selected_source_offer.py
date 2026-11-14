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


def _resolve(service, target: SourceOfferTarget):
    """Recheck the exact account and live delivery ABI without provider I/O."""
    if type(target) is not SourceOfferTarget:
        raise ValueError("selected_source_target_invalid")
    # Execution is headless: compare only the captured context generation.
    # Production LibraryService publishes a monotone epoch on every identity
    # switch; minimal synthetic facades with no epoch still need all the
    # independent account/session/ABI binding guards below.
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
    return target, account


def select_source_offer_target(
    service, *, provider_id: str, credential_id: str, item_id: str,
    identity_id: str | None = None,
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
                or (type(epoch) is int and epoch < 0)):
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
    candidates = [
        row for row in (entry.get("possessions") or ())
        if type(row) is dict and row.get("identity_id") == identity
        and row.get("kind") == "owned" and row.get("state") == "active"
    ] if type(entry.get("possessions")) in (tuple, list) else []
    if not candidates:
        # A catalogue observation is never a pseudo-possession. Its exact
        # private origin can be selected passively; current delivery remains
        # a separate producer callback after explicit user action.
        refs = entry.get("source_refs")
        if (entry.get("source") != "private_catalog_observation"
                or entry.get("state") != "remote_catalog"
                or type(refs) not in (tuple, list) or len(refs) != 1
                or type(refs[0]) is not dict):
            return None
        candidate = refs[0]
        if (candidate.get("identity_id") != identity
                or candidate.get("kind") != "purchase"):
            return None
    elif len(candidates) == 1:
        candidate = candidates[0]
    else:
        return None
    provider = candidate.get("provider_id")
    item_id = candidate.get("item_id")
    account_id = candidate.get("account_id")
    accounts = [
        account for account in service.accounts
        if getattr(account, "identity_id", None) == identity
        and getattr(account, "provider_id", None) == provider
        and getattr(account, "unique_id", None) == account_id
    ]
    if len(accounts) != 1:
        return None
    return select_source_offer_target(
        service, provider_id=provider, credential_id=accounts[0].credential_id,
        item_id=item_id, identity_id=identity,
    )
