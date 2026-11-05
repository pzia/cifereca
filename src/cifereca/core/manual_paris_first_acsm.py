"""#3121: private, one-shot handoff of the first Paris loan ACSM offer.

The first ASSARedirect may create a loan and produce a signed ACSM location
in one HTTP operation. The confirmed Loan is not returned by the public
manual-borrow result. Retain this *transient* location only after the exact
Core claim has been durably confirmed. It is never a right, an ACSM receipt,
a delivery proof, a JSON value or a reason to repeat the mutating request.

No persistence by design: a process restart cannot recover a signed URL.
A later, independent authenticated loan inventory may supply its own direct
EbookUrl; otherwise this module must never invent one.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
import threading
import time
from urllib.parse import urlsplit

from ..models import Account, Loan, SearchResult

_TTL_SECONDS = 15 * 60
_MAX_OFFERS = 12
_GUARD = threading.RLock()


@dataclass(frozen=True, slots=True, repr=False)
class _Offer:
    account: Account = field(repr=False)
    session: object = field(repr=False)
    identity_id: str
    provider_id: str
    credential_id: str
    borrower: str
    loan_id: str
    edition_id: str
    claim_key: str
    operation_id: str
    context_epoch: int
    created: float
    url: str = field(repr=False)


def _valid_url(value: object) -> bool:
    """Minimal bounded transport shape; the Paris plugin revalidates peers."""
    if (type(value) is not str or not 0 < len(value) <= 4096
            or any(ord(c) < 0x20 or ord(c) == 0x7f for c in value)):
        return False
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme == "https"
            and type(parsed.hostname) is str and bool(parsed.hostname)
            and parsed.username is None and parsed.password is None
            and parsed.port in (None, 443)
            and not parsed.fragment
        )
    except (ValueError, AttributeError):
        return False


def _key(account: Account, loan: Loan) -> tuple[str, ...]:
    return (
        account.identity_id, account.provider_id, account.credential_id,
        account.unique_id, loan.id, str(loan.record_id),
    )


def _offers(service: object) -> dict[tuple[str, ...], _Offer]:
    entries = getattr(service, "_first_paris_acsm_offers", None)
    if type(entries) is not dict:
        entries = {}
        setattr(service, "_first_paris_acsm_offers", entries)
    now = time.monotonic()
    for key, old in tuple(entries.items()):
        if (type(old) is not _Offer or now < old.created
                or now - old.created >= _TTL_SECONDS):
            entries.pop(key, None)
    return entries


def _current(service: object, account: Account, session: object,
             epoch: int) -> bool:
    try:
        return (
            type(account) is Account
            and account.provider_id == "paris"
            and account.is_primary is True
            and account.unique_id == ""
            and type(account.identity_id) is str and bool(account.identity_id)
            and type(epoch) is int
            and service._account_context_epoch == epoch
            and getattr(service.current_identity, "identity_id", None)
                == account.identity_id
            and sum(a is account for a in service.accounts) == 1
            and service.storage.identity_for_connection(
                account.provider_id, account.credential_id,
            ) == account.identity_id
            and service._session_for_account(account) is session
        )
    except (AttributeError, KeyError, OSError, TypeError, ValueError):
        return False


def remember_confirmed_first_acsm(
    service, *, item: SearchResult, loan: Loan, account: Account,
    session: object, claim_key: str, operation_id: str,
    context_epoch: int,
) -> bool:
    """Preserve the exact first response ONLY after confirmed durable claim.

    The original owner and borrow scope must still be live. No unverified
    provider return, no foreign item, no arbitrary URL or pending claim can
    enter the private handoff.
    """
    if (type(item) is not SearchResult or type(loan) is not Loan
            or type(claim_key) is not str or not claim_key.startswith("loan.borrow.v1:")
            or type(operation_id) is not str or not operation_id
            or not _valid_url(loan.ebook_url)
            or loan.provider != "DILICOM" or loan.medium != "digital"
            or loan.source_provider != "paris" or loan.id == ""
            or type(loan.id) is not str or not loan.record_id
            or str(loan.record_id) != item.record_id
            or loan.identity_id != account.identity_id
            or loan.source_credential != account.credential_id
            or loan.source_account_id != "primary"
            or item.source_provider != "paris"
            or item.source_credential != account.credential_id
            or not _current(service, account, session, context_epoch)):
        return False
    try:
        row = service.storage.loan_borrow_state(claim_key)
        if (type(row) is not dict or row.get("status") != "confirmed"
                or row.get("operation_id") != operation_id
                or row.get("verified_loan_id") != loan.id):
            return False
    except Exception:
        return False
    with _GUARD:
        entries = _offers(service)
        if len(entries) >= _MAX_OFFERS and _key(account, loan) not in entries:
            return False
        entries[_key(account, loan)] = _Offer(
            account, session, account.identity_id, account.provider_id,
            account.credential_id, account.unique_id, loan.id,
            str(loan.record_id), claim_key, operation_id,
            context_epoch, time.monotonic(), loan.ebook_url,
        )
    return True


def take_first_acsm_loan(service, loan: Loan, account: Account) -> Loan | None:
    """Consume the exact one-shot first offer for a freshly observed loan.

    The returned copy is PRIVATE and is delivered only to the existing
    Paris download boundary, which independently checks URL, account and
    staging. No claim/request status is changed and no network runs here.
    """
    if (type(loan) is not Loan or type(account) is not Account
            or loan.source_provider != "paris"
            or loan.source_credential != account.credential_id
            or loan.source_account_id != "primary"
            or loan.identity_id != account.identity_id
            or loan.provider != "DILICOM" or loan.medium != "digital"
            or not loan.id or not loan.record_id):
        return None
    with _GUARD:
        record = _offers(service).pop(_key(account, loan), None)
    if record is None:
        return None
    if (record.account is not account
            or record.identity_id != account.identity_id
            or record.provider_id != account.provider_id
            or record.credential_id != account.credential_id
            or record.borrower != account.unique_id
            or record.loan_id != loan.id
            or record.edition_id != str(loan.record_id)
            or not _current(service, account, record.session,
                            record.context_epoch)):
        return None
    try:
        claim = service.storage.loan_borrow_state(record.claim_key)
        if (type(claim) is not dict or claim.get("status") != "confirmed"
                or claim.get("operation_id") != record.operation_id
                or claim.get("verified_loan_id") != record.loan_id):
            return None
        return replace(loan, ebook_url=record.url)
    except Exception:
        return None


__all__ = ["remember_confirmed_first_acsm", "take_first_acsm_loan"]
