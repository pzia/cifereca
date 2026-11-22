"""Existing-loan documentary intent: cache-only repair of an exact public C.

No private title/borrower coordinate enters the durable journal. The public
source is independently admitted; an active loan is only the trigger to
repair its missing self-C. No new parser/network operation or scheduler.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from sqlite3 import Error as SQLiteError
import time

from ..models import Loan
from ..plugin_api.loan_scope import loan_matches_account
from .notice_c_authenticated_web_choices import _capture
from .notice_c_borrower_public_projection import (
    _attested_public_edition, _coordinate, _declared_branches, _fold,
)
from .notice_c_exact_source_lookup import _valid_coordinates
from .notice_c_initial_publication import _inputs, request_initial_publication_txn
from .notice_c_private_search_web import _sql_binding_current
from .notice_cover_projection import notice_cover_document_ref
from .notice_search_cover_producer import _coordinate_digest
from .notice_source_observation import NoticeSourceKey
from .search_orchestration import capture_search_read_scope
from .source_context import source_context_mutation

_MAX_REPAIRS = 32


@dataclass(frozen=True, slots=True, repr=False)
class ExistingLoanCacheRepair:
    loan: Loan = field(compare=False)
    account_epoch: int
    source_epoch: int


def _public_key(coords):
    provider, record, medium, docbase, revision = coords
    if not _valid_coordinates((provider, record, docbase, medium, revision)):
        return None
    return NoticeSourceKey(
        provider, "public", None, None, None, "search.public.catalogue",
        "search:" + _coordinate_digest(provider, "public_catalog", record),
        "edition:" + _coordinate_digest(
            provider, "public_catalog", record, docbase, medium, revision),
        "digital",
    )


def request_existing_loan_cache_repairs(service, rows, *, identity_id,
                                       account_epoch, source_epoch, descriptors, now=None):
    """Worker-only, after the existing scoped native inventory read.

    Admit only image-less, active, exactly attributed loans with an already
    stored public Search v2 observation. Missing raw data, unknown source
    contracts or revoked accounts are unsupported; never search for them.
    The returned refs are a bounded repair batch, not inventory completeness.
    """
    if (type(rows) is not list or not 0 < len(rows) <= 2048
            or type(identity_id) is not str or not identity_id
            or type(account_epoch) is not int or account_epoch < 0
            or type(source_epoch) is not int or source_epoch < 0):
        return ()
    try:
        scope = capture_search_read_scope(service, identity_id)
        storage = service.storage
        if scope.source_epoch != source_epoch:
            return ()
    except (AttributeError, LookupError, TypeError, ValueError, RuntimeError):
        return ()
    now = int(time.time()) if now is None else now
    declarations = _declared_branches(descriptors)
    refs = []
    seen = set()

    def current():
        return (service.storage is storage
                and service._account_context_epoch == account_epoch
                and scope.current(service))

    for loan in rows:
        if len(refs) >= _MAX_REPAIRS or not current():
            break
        if (type(loan) is not Loan or type(loan.state) is not str or loan.state != "active"
                or _coordinate(loan, identity_id) is None
                or not loan.source_account_id):
            continue
        coords = _attested_public_edition(loan, declarations)
        if coords is None or (key := _public_key(coords)) is None:
            continue
        intent = ExistingLoanCacheRepair(loan, account_epoch, source_epoch)
        try:
            with source_context_mutation(service):
                if not current() or intent.loan is not loan:
                    break
                private = notice_cover_document_ref(NoticeSourceKey(
                    loan.source_provider, "private", identity_id, loan.source_credential,
                    loan.source_account_id, "loan.enrichment.trigger",
                    "loan", "edition", "digital",
                ))
                binding = _capture(service, scope, private)
                if binding is None or not loan_matches_account(loan, binding.account, binding.descriptor):
                    continue
                public = notice_cover_document_ref(key)
                if public.source_key in seen:
                    continue
                with storage._lock, storage._connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    if not _sql_binding_current(db, private, binding.connection_epoch):
                        continue
                    source, _document, _digest = _inputs(db, public.source_key)
                    bio = source.observation.fields_copy()["search_result"]["public_bibliography"]
                    if (_fold(loan.title) != _fold(bio["title"])
                            or (loan.author and bio.get("author") and _fold(loan.author) != _fold(bio["author"]))):
                        continue
                    # No repair if the independently admitted source changed.
                    if not current():
                        raise ValueError("loan_enrichment_scope_changed")
                    ref = request_initial_publication_txn(db, source, now=now)
                    if not current():
                        raise ValueError("loan_enrichment_scope_changed")
                seen.add(public.source_key)
                if ref is not None:
                    refs.append(ref)
        except (AttributeError, KeyError, LookupError, TypeError, ValueError,
                RuntimeError, SQLiteError):
            continue
    return tuple(refs)
