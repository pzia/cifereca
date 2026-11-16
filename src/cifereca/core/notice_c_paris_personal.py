"""P0 #6366: real Paris digital-loan/history upstream record -> PUBLIC C.

ONLY Paris's DILICOM account service and public SearchService share the same
exact source coordinates: Loan/Handing.RecordId -> Search.Resource.RscId
under RscBase=DILICOM. The *already published* public C must independently
verify the full provider/record/docbase/medium/revision combination.

This is NOT an account -> C identity join, an entitlement, or a licence.
A source-specific worker may attach an ephemeral display-only receipt after
an exact bounded Core C read. Nothing is written to the account's cache,
published in public C, or inferred from title, ISBN, URL, loan ID or SHA.
Other providers, physical books, missing source fields and ambiguous C fail
closed until their respective upstream contracts are established.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models import Account, HistoryItem, Loan
from .book_cover_projection import CoverDocumentScope
from .notice_c_cover_index import is_canonical_cover_document
from .notice_c_exact_source_lookup import exact_public_source_c

_CORE_PARIS_PERSONAL_C_WITNESS = object()
_LINK_FIELD = "_core_notice_c_paris_public_link"
_MAX_PERSONAL = 256


@dataclass(frozen=True, slots=True, eq=False)
class VerifiedParisPersonalPublicC:
    """Private, one-row, one-epoch, one-account link to PUBLIC C image N2."""

    original: Loan | HistoryItem
    document: CoverDocumentScope
    canonical_epoch: int
    scope: tuple[str, str, str, str]
    account_epoch: int
    source_epoch: int
    record_id: str
    witness: object


def attach_paris_personal_public_c(
    storage: Any, items: list[Loan] | list[HistoryItem], *,
    account: Account, account_epoch: int, source_epoch: int,
) -> None:
    """Worker-only annotator for already account-lease-verified DTOs.

    Accept a provider-emitted RecordId exclusively for the Paris DILICOM
    branch; it is not a generic loan-record matcher. The public source's C
    is independently admitted by the anonymous public Search producer.
    """
    if type(items) is not list:
        return
    # Replayed/adapted objects cannot retain another worker's old receipt.
    for item in items[:_MAX_PERSONAL + 1]:
        if type(item) in (Loan, HistoryItem):
            item.__dict__.pop(_LINK_FIELD, None)
    if (type(account) is not Account or account.provider_id != "paris"
            or type(account.identity_id) is not str or not account.identity_id
            or type(account.credential_id) is not str
            or not account.credential_id
            or type(account.unique_id) is not str
            or type(account_epoch) is not int or account_epoch < 0
            or type(source_epoch) is not int or source_epoch < 0
            or not items or len(items) > _MAX_PERSONAL):
        return
    from ..storage import Storage
    if not isinstance(storage, Storage):
        return
    scope = (
        account.identity_id, account.provider_id,
        account.credential_id, account.unique_id,
    )
    from .notice_c_cover_index import _IDENTIFIER
    matched: dict[str, object] = {}
    missed: set[str] = set()
    for item in items:
        if (type(item) not in (Loan, HistoryItem)
                or item.provider != "DILICOM"
                or item.medium != "digital"
                or item.source_provider != account.provider_id
                or item.source_credential != account.credential_id
                or item.identity_id not in (None, account.identity_id)
                or item.source_account_id not in (None, account.unique_id)
                or type(item.record_id) is not str
                or _IDENTIFIER.fullmatch(item.record_id) is None
                or "://" in item.record_id or ".." in item.record_id):
            continue
        record = item.record_id
        if record not in matched and record not in missed:
            # Paris's public Search projection intentionally emits revision
            # None. Never attach to a different revised record by title.
            result = exact_public_source_c(
                storage, provider_id="paris", record_id=record,
                docbase="DILICOM", medium="digital",
                source_revision=None,
            )
            if result is None or not result.candidates:
                missed.add(record)
                continue
            matched[record] = result
        canonical = matched.get(record)
        if canonical is None:
            continue
        item.__dict__[_LINK_FIELD] = VerifiedParisPersonalPublicC(
            original=item, document=canonical.document,
            canonical_epoch=canonical.epoch, scope=scope,
            account_epoch=account_epoch, source_epoch=source_epoch,
            record_id=record, witness=_CORE_PARIS_PERSONAL_C_WITNESS,
        )


def valid_paris_personal_public_c(
    link: object, *, original: object, active_identity: object,
    active_account: object, account_epoch: object, source_epoch: object,
) -> bool:
    """No SQLite/HTTP at GTK time. Exact row+owner+two epochs only."""
    if (type(link) is not VerifiedParisPersonalPublicC
            or link.witness is not _CORE_PARIS_PERSONAL_C_WITNESS
            or type(original) not in (Loan, HistoryItem)
            or original is not link.original
            or original.__dict__.get(_LINK_FIELD) is not link
            or not is_canonical_cover_document(link.document)
            or type(link.canonical_epoch) is not int or link.canonical_epoch < 1
            or type(active_identity) is not str or not active_identity
            or type(active_account) is not Account
            or type(account_epoch) is not int or type(source_epoch) is not int
            or account_epoch != link.account_epoch
            or source_epoch != link.source_epoch
            or (active_identity, active_account.provider_id,
                active_account.credential_id, active_account.unique_id)
                != link.scope):
        return False
    return (original.provider == "DILICOM"
            and original.medium == "digital"
            and original.source_provider == "paris"
            and original.source_credential == link.scope[2]
            and original.source_account_id in (None, link.scope[3])
            and original.identity_id in (None, link.scope[0])
            and original.record_id == link.record_id)


__all__ = [
    "VerifiedParisPersonalPublicC", "attach_paris_personal_public_c",
    "valid_paris_personal_public_c",
]
