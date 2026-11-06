"""#4662: inert, exact-account multi-profile borrow opportunity preview.

The selected reader is a *destination*, never the source account of a loan.
Only the lender's independently scoped existing Core capability/eligibility
observations qualify a candidate. Neither these facts nor this presentation
are consent, proof of supplier acceptance or inter-identity transfer rights.

No calls to providers, Storage, GUI, network or mutation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from typing import Any

from ..models import Account, SearchResult
from ..plugin_api.lending_capabilities import (
    BorrowerOperationEligibility, LendingOperationFact,
)
from .borrow_action_scope import borrow_claim_key

_MAX_OFFERS = 64
_MAX_AGE = 300
_MAX_ID = 160
_MAX_NAME = 80


@dataclass(frozen=True, slots=True, repr=False)
class BorrowForIdentityWitness:
    """Core-attested immutable-view *inputs* from one exact lender account.

    Producer must independently verify live session registration, identity
    ownership and exact edition. This DTO is not a provider permission.
    """

    account: Account = field(repr=False)
    notice: SearchResult = field(repr=False)
    capability: LendingOperationFact = field(repr=False)
    eligibility: BorrowerOperationEligibility = field(repr=False)
    lender_name: str
    observed_at: int
    service_context_epoch: int
    source_epoch: int
    edition_id: str
    # Explicit negative or UNKNOWN transfer assessment: never infer legal
    # usage/copy rights from an observed ability to borrow.
    transfer_state: str = "unknown"


@dataclass(frozen=True, slots=True)
class BorrowForIdentityOption:
    option_id: str
    borrowing_identity_id: str
    lender_name: str
    provider_id: str
    edition_id: str
    label: str
    transfer_state: str
    # Internal exact coordinates must NOT be rendered as a user-visible label.
    # Still no authority for a callback: re-resolve all facts in fresh Core.
    _source_coordinates: tuple[str, str, str, str, str, str] = field(repr=False)
    executable: bool = False


@dataclass(frozen=True, slots=True)
class BorrowForIdentityPreview:
    requesting_identity_id: str
    status: str  # none | own | external | choose
    label: str
    options: tuple[BorrowForIdentityOption, ...]
    rejected_count: int
    requires_explicit_choice: bool
    # Never a lending claim or mutation/transfer approval.
    executable: bool = False


def _id(value: object) -> bool:
    return (type(value) is str and 0 < len(value) <= _MAX_ID
            and value.isprintable() and value == value.strip()
            and all(c.isascii() and (c.isalnum() or c in "-._:/")
                    for c in value))


def _name(value: object) -> bool:
    return (type(value) is str and 0 < len(value) <= _MAX_NAME
            and value.isprintable() and value == value.strip()
            and "\x00" not in value)


def _eligible(w: BorrowForIdentityWitness, *, now: int,
              context_epoch: int, requesting_identity_id: str,
              expected_edition_id: str) -> bool:
    """All coordinates are cross-checked, never inferred from a title/ISBN."""
    a, item, cap, eligible = w.account, w.notice, w.capability, w.eligibility
    if (type(a) is not Account or type(item) is not SearchResult
            or type(cap) is not LendingOperationFact
            or type(eligible) is not BorrowerOperationEligibility
            or not _name(w.lender_name)
            or not all(_id(v) for v in (
                a.identity_id, a.provider_id, a.credential_id,
                item.id, item.record_id, item.docbase, item.source_provider,
                item.source_credential, w.edition_id,
            ))
            or type(a.unique_id) is not str
            or len(a.unique_id) > 160 or "\x00" in a.unique_id
            or not a.unique_id.isprintable()
            or type(a.is_primary) is not bool or a.is_primary is not True
            or item.medium != "digital"
            or item.source_provider != a.provider_id
            or item.source_credential != a.credential_id
            or (item.source_collection is not None and (
                not _id(item.source_collection)
            ))
            or w.edition_id != expected_edition_id
            or not _id(requesting_identity_id)
            or type(w.service_context_epoch) is not int
            or w.service_context_epoch != context_epoch
            or type(w.source_epoch) is not int or w.source_epoch < 0
            or type(w.observed_at) is not int
            or not 0 <= now - w.observed_at <= _MAX_AGE
            or w.transfer_state not in ("unknown", "blocked", "admissible_unverified")
            or cap.operation != "loan.borrow" or cap.availability != "supported"
            or cap.usable is not True
            or cap.manifest_declared is not True
            or cap.descriptor_declared is not True
            or cap.method_implemented is not True
            or cap.session_ready is not True
            or eligible.operation != "loan.borrow"
            or eligible.branch != item.docbase
            or eligible.state != "eligible_observed"
            or eligible.reason_code != "borrow_permission_observed"
            or borrow_claim_key(a, item) is None):
        return False
    return True


def preview_borrow_for_identity(
    *, requesting_identity_id: str, expected_edition_id: str,
    witnesses: object, now: int, context_epoch: int,
) -> BorrowForIdentityPreview:
    """Render 0 / 1 / N choices, without creating or choosing an operation.

    A missing/blocked/ambiguous account is not promoted to an option. A
    repeated exact lender+offer is de-duplicated only when every pinned
    coordinate is identical; conflicting duplicate attestations block the
    *entire* preview, never select the first by order.
    """
    if (not _id(requesting_identity_id) or not _id(expected_edition_id)
            or type(now) is not int or now < 1
            or type(context_epoch) is not int or context_epoch < 0
            or type(witnesses) not in (list, tuple)
            or len(witnesses) > _MAX_OFFERS):
        raise ValueError("invalid_cross_profile_borrow_scope")
    options: dict[tuple[str, str, str, str, str, str], BorrowForIdentityOption] = {}
    rejected = 0
    for w in witnesses:
        if type(w) is not BorrowForIdentityWitness:
            raise ValueError("invalid_cross_profile_borrow_witness")
        if not _eligible(w, now=now, context_epoch=context_epoch,
                         requesting_identity_id=requesting_identity_id,
                         expected_edition_id=expected_edition_id):
            rejected += 1
            continue
        a, item = w.account, w.notice
        # The unique account ID can be empty for one legacy primary account;
        # credential and identity prevent a fallback to another account.
        coordinates = (
            a.identity_id, a.provider_id, a.credential_id,
            a.unique_id, item.docbase, item.record_id,
        )
        route = (item.source_collection or "", item.medium,
                 w.edition_id, w.source_epoch)
        full = coordinates + route
        option_id = "borrow-profile.v1:" + sha256(json.dumps(
            (requesting_identity_id, full), ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        label = ("Emprunter" if a.identity_id == requesting_identity_id else
                 "Emprunter avec " + w.lender_name)
        output = BorrowForIdentityOption(
            option_id=option_id, borrowing_identity_id=a.identity_id,
            lender_name=w.lender_name, provider_id=a.provider_id,
            edition_id=w.edition_id, label=label,
            transfer_state=w.transfer_state,
            _source_coordinates=coordinates,
        )
        existing = options.get(coordinates)
        if existing is not None:
            if existing != output:
                # Same account+notice, but different edition/epoch/transfer
                # observation. This cannot be resolved by list order.
                raise ValueError("conflicting_cross_profile_borrow_offer")
            continue
        options[coordinates] = output
    ordered = tuple(sorted(options.values(), key=lambda x: (
        x.borrowing_identity_id, x.provider_id, x.lender_name, x.option_id,
    )))
    if not ordered:
        status, label, requires_choice = "none", "Emprunt non vérifié", False
    elif len(ordered) == 1:
        status = "own" if ordered[0].borrowing_identity_id == requesting_identity_id else "external"
        label = ordered[0].label
        requires_choice = False  # Still requires click + Core final checks.
    else:
        status, label, requires_choice = "choose", "Emprunter avec…", True
    return BorrowForIdentityPreview(
        requesting_identity_id=requesting_identity_id,
        status=status, label=label, options=ordered,
        rejected_count=rejected, requires_explicit_choice=requires_choice,
    )


__all__ = [
    "BorrowForIdentityWitness", "BorrowForIdentityOption",
    "BorrowForIdentityPreview", "preview_borrow_for_identity",
]
