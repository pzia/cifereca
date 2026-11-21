"""#7161: scoped read-model and individual budget reset, no provider I/O."""
from __future__ import annotations

from dataclasses import dataclass
from inspect import getattr_static

from ..locking import InterProcessLock
from ..models import Account, Loan
from ..paths import acquisition_lock_file
from ..plugin_api.downloadable_resources import SourceReference
from ..plugin_api.provider_rights_observation import ProviderRightsScope
from .canonical_adept_attempt import _key as _adept_key
from .canonical_recovery import _exact_borrower_bound_in_db
from .canonical_runtime_attempt import RUNTIME_ATTEMPT_PREFIX, read_runtime_attempt_in_db
from .canonical_loan_retry_guard import historical_adept_ledger_hold
from ..plugin_api.loan_inventory import LoanInventoryScope
from .loan_download_retry import (
    LoanDownloadRetry, _INTERVENTION_PREFIX, _OWNED_KEY_PREFIX, _decode, _key,
    _retry_connection, read_adept_intervention,
)
from .notice_c_purchase_descriptive_read import _read_descriptive_txn
from .source_context import source_context_epoch
from .source_convergence import SourceKey


@dataclass(frozen=True, slots=True)
class LoanDownloadRetryRow:
    source: SourceKey
    title: str
    retry: LoanDownloadRetry
    adept_hold: bool
    inactive: bool
    profile_refused_at: int = 0
    runtime_hold: bool = False
    runtime_started_at: int = 0
    runtime_phase: str = ""

    @property
    def can_reset(self) -> bool:
        return self.retry.paused and not (
            self.adept_hold or self.inactive or self.profile_refused_at or self.runtime_hold
        )


_MAX_SOURCES = 2048  # actual review rows, not the historical library
_MAX_SCANNED_SOURCES = 100_000
_SCAN_BATCH = 128


def _context(service, identity_id):
    storage = service.storage
    epoch = source_context_epoch(service)
    account_epoch = getattr(service, "_account_context_epoch", None)
    def current():
        return (
            type(identity_id) is str and bool(identity_id)
            and getattr(service.current_identity, "identity_id", None) == identity_id
            and service.storage is storage
            and source_context_epoch(service) == epoch
            and getattr(service, "_account_context_epoch", None) == account_epoch
        )
    if not current():
        raise ValueError("loan_download_review_scope_changed")
    return storage, current


def _title_index(service, identity_id):
    titles = {}
    for loan in getattr(service, "last_loans", ()) or ():
        if (type(loan) is Loan and loan.identity_id == identity_id
                and loan.medium == "digital" and type(loan.title) is str
                and loan.title.strip()):
            key = (loan.source_provider, loan.source_credential,
                   loan.source_account_id, loan.record_id or loan.id, loan.id)
            titles.setdefault(key, set()).add(loan.title)
    return titles


def _inventory_scopes(service, identity_id):
    """Derive aliases only from the current descriptor, never provider labels."""
    scopes = {}
    for account in getattr(service, "accounts", ()):
        if type(account) is not Account or account.identity_id != identity_id:
            continue
        key = (account.provider_id, account.credential_id, account.unique_id)
        try:
            session = service._sessions.get(service._session_key(
                account.provider_id, account.credential_id,
            ))
            scope = LoanInventoryScope.for_account(
                account, descriptor=getattr_static(session, "descriptor", None),
            )
        except (ValueError, AttributeError, TypeError):
            continue
        if key in scopes:
            scopes[key] = None  # never infer an alias from duplicate bindings
        else:
            scopes[key] = scope
    return scopes


def _ledger_hold(db, source, scopes):
    scope = source.scope
    observed = scopes.get((scope.provider_id, scope.credential_id, scope.account_id))
    if observed is None:
        observed = LoanInventoryScope(scope.identity_id, scope.provider_id,
                                      scope.credential_id, scope.account_id)
    return historical_adept_ledger_hold(db, observed, source)


def _has_adept_hold(db, source, scopes):
    return (db.execute("SELECT 1 FROM metadata WHERE key=?", (_adept_key(source),)).fetchone() is not None
            or _ledger_hold(db, source, scopes))


def review_loan_download_retries(service, *, identity_id: str):
    """Read exact locally observed resources without an inventory request."""
    storage, current = _context(service, identity_id)
    titles = _title_index(service, identity_id)
    scopes = _inventory_scopes(service, identity_id)
    result = []
    with _retry_connection(storage) as db:
        db.execute("BEGIN")
        sources = db.execute(
            "SELECT ref.provider_id,ref.credential_id,ref.account_id,ref.branch_id,"
            "ref.edition_id,ref.right_id,ref.state,ref.reference_kind "
            "FROM source_references ref WHERE ref.identity_id=? "
            "AND ref.evidence_kind='provider_response' AND ref.evidence_ref<>'' "
            "AND (ref.reference_kind='loan' OR (ref.reference_kind='purchase' AND EXISTS ("
            "SELECT 1 FROM metadata WHERE key IN (? || ref.source_reference_id, "
            "? || ref.source_reference_id, ? || ref.source_reference_id)))) "
            "ORDER BY ref.provider_id,ref.credential_id,ref.account_id,ref.edition_id,"
            "ref.right_id,ref.branch_id,ref.reference_kind LIMIT ?",
            (identity_id, _OWNED_KEY_PREFIX, _INTERVENTION_PREFIX,
             RUNTIME_ATTEMPT_PREFIX, _MAX_SCANNED_SOURCES + 1),
        )
        # Legacy loan marker keys use a different hash than SourceReference.
        # Stream exact rows in the same snapshot, instead of materializing
        # the entire history or truncating the first 2048 observations.
        scanned = 0
        def observed_sources():
            nonlocal scanned
            while True:
                batch = sources.fetchmany(_SCAN_BATCH)
                if not batch:
                    return
                for coordinates in batch:
                    scanned += 1
                    if scanned > _MAX_SCANNED_SOURCES:
                        raise ValueError("loan_download_review_scan_over_budget")
                    yield coordinates
        for provider, credential, account, branch, edition, right, state, kind in observed_sources():
            source = SourceKey(ProviderRightsScope(identity_id, provider, credential, account),
                               edition, right, kind, branch)
            raw = db.execute("SELECT value FROM metadata WHERE key=?", (_key(source),)).fetchone()
            retry = _decode(raw[0] if raw is not None else None)
            hold = kind == "loan" and _has_adept_hold(db, source, scopes)
            refused_at = read_adept_intervention(db, source)
            runtime_hold, runtime_started_at, runtime_phase = read_runtime_attempt_in_db(db, source)
            if not (retry.failures or hold or refused_at or runtime_hold):
                continue
            if len(result) >= _MAX_SOURCES:
                raise ValueError("loan_download_review_over_budget")
            title = "Référence " + edition
            if kind == "loan":
                possible = titles.get((provider, credential, account, edition, right), ())
                if len(possible) == 1:
                    title = next(iter(possible))
            else:
                try:
                    reference = SourceReference(source.scope, branch, edition, right, kind)
                except ValueError:
                    reference = None
                if reference is not None:
                    notice = _read_descriptive_txn(db, reference)
                    if notice is not None:
                        title = next((fact.value for fact in notice.facts
                                      if fact.field == "title"), title)
            result.append(LoanDownloadRetryRow(
                source, title, retry, hold, state == "inactive", refused_at,
                runtime_hold, runtime_started_at, runtime_phase,
            ))
    if not current():
        raise ValueError("loan_download_review_scope_changed")
    return tuple(result)


def reset_reviewed_loan_download_retry(service, *, identity_id: str,
                                     row: LoanDownloadRetryRow) -> None:
    """Reset only the displayed, unchanged negative budget, for the next Sync.

    This is a local explicit-user action, not a grant, fetch, or ADEPT unlock.
    The ordinary canonical admission and one-use fences still apply.
    """
    if (type(row) is not LoanDownloadRetryRow or not row.can_reset
            or row.source.scope.identity_id != identity_id):
        raise ValueError("loan_download_retry_reset_refused")
    storage, current = _context(service, identity_id)
    source = row.source
    scope = source.scope
    scopes = _inventory_scopes(service, identity_id)
    if storage._connect().in_transaction:
        raise ValueError("loan_download_retry_nested_transaction")
    with InterProcessLock(acquisition_lock_file()):
        with _retry_connection(storage) as db:
            db.execute("BEGIN IMMEDIATE")
            if not current() or not _exact_borrower_bound_in_db(db, scope):
                raise ValueError("loan_download_review_scope_changed")
            refs = db.execute(
                "SELECT state FROM source_references WHERE identity_id=? AND provider_id=? "
                "AND credential_id=? AND account_id=? AND branch_id=? AND edition_id=? "
                "AND right_id=? AND reference_kind=? AND evidence_kind='provider_response' "
                "AND evidence_ref<>'' LIMIT 2",
                (scope.identity_id, scope.provider_id, scope.credential_id, scope.account_id,
                 source.branch, source.edition_id, source.right_id, source.kind),
            ).fetchall()
            raw = db.execute("SELECT value FROM metadata WHERE key=?", (_key(source),)).fetchone()
            if (len(refs) != 1 or refs[0][0] == "inactive"
                    or (source.kind == "loan" and _has_adept_hold(db, source, scopes))
                    or read_adept_intervention(db, source)
                    or read_runtime_attempt_in_db(db, source)[0]
                    or raw is None or _decode(raw[0]) != row.retry or not current()):
                raise ValueError("loan_download_retry_reset_changed")
            db.execute("DELETE FROM metadata WHERE key=?", (_key(source),))
