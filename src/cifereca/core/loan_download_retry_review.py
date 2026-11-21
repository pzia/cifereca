"""#7161: scoped read-model and individual budget reset, no provider I/O."""
from __future__ import annotations

from dataclasses import dataclass
from inspect import getattr_static

from ..locking import InterProcessLock
from ..models import Account, Loan
from ..paths import acquisition_lock_file
from ..plugin_api.provider_rights_observation import ProviderRightsScope
from .canonical_adept_attempt import _key as _adept_key
from .canonical_recovery import _exact_borrower_bound_in_db
from .canonical_loan_retry_guard import historical_adept_ledger_hold
from ..plugin_api.loan_inventory import LoanInventoryScope
from .loan_download_retry import LoanDownloadRetry, _decode, _key
from .source_context import source_context_epoch
from .source_convergence import SourceKey


@dataclass(frozen=True, slots=True)
class LoanDownloadRetryRow:
    source: SourceKey
    title: str
    retry: LoanDownloadRetry
    adept_hold: bool
    inactive: bool

    @property
    def can_reset(self) -> bool:
        return self.retry.paused and not self.adept_hold and not self.inactive


_MAX_SOURCES = 2048


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


def review_loan_download_retries(service, *, identity_id: str):
    """Read only exact locally observed loan references, never an inventory GET."""
    storage, current = _context(service, identity_id)
    titles = _title_index(service, identity_id)
    scopes = _inventory_scopes(service, identity_id)
    result = []
    with storage._lock, storage._connect() as db:
        db.execute("BEGIN")
        sources = db.execute(
            "SELECT provider_id,credential_id,account_id,branch_id,edition_id,right_id,state "
            "FROM source_references WHERE identity_id=? AND reference_kind='loan' "
            "AND evidence_kind='provider_response' AND evidence_ref<>'' "
            "ORDER BY provider_id,credential_id,account_id,edition_id,right_id,branch_id LIMIT ?",
            (identity_id, _MAX_SOURCES + 1),
        ).fetchall()
        if len(sources) > _MAX_SOURCES:
            raise ValueError("loan_download_review_over_budget")
        for provider, credential, account, branch, edition, right, state in sources:
            source = SourceKey(ProviderRightsScope(identity_id, provider, credential, account),
                               edition, right, "loan", branch)
            raw = db.execute("SELECT value FROM metadata WHERE key=?", (_key(source),)).fetchone()
            retry = _decode(raw[0] if raw is not None else None)
            hold = (db.execute("SELECT 1 FROM metadata WHERE key=?", (_adept_key(source),)).fetchone() is not None
                    or _ledger_hold(db, source, scopes))
            if not retry.failures and not hold:
                continue
            possible = titles.get((provider, credential, account, edition, right), ())
            title = next(iter(possible)) if len(possible) == 1 else "Référence " + edition
            result.append(LoanDownloadRetryRow(
                source, title, retry, hold, state == "inactive",
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
    with InterProcessLock(acquisition_lock_file()):
        with storage._lock, storage._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not current() or not _exact_borrower_bound_in_db(db, scope):
                raise ValueError("loan_download_review_scope_changed")
            refs = db.execute(
                "SELECT state FROM source_references WHERE identity_id=? AND provider_id=? "
                "AND credential_id=? AND account_id=? AND branch_id=? AND edition_id=? "
                "AND right_id=? AND reference_kind='loan' AND evidence_kind='provider_response' "
                "AND evidence_ref<>'' LIMIT 2",
                (scope.identity_id, scope.provider_id, scope.credential_id, scope.account_id,
                 source.branch, source.edition_id, source.right_id),
            ).fetchall()
            raw = db.execute("SELECT value FROM metadata WHERE key=?", (_key(source),)).fetchone()
            hold = db.execute("SELECT 1 FROM metadata WHERE key=?", (_adept_key(source),)).fetchone()
            if (len(refs) != 1 or refs[0][0] == "inactive" or hold is not None
                    or _ledger_hold(db, source, scopes)
                    or raw is None or _decode(raw[0]) != row.retry or not current()):
                raise ValueError("loan_download_retry_reset_changed")
            db.execute("DELETE FROM metadata WHERE key=?", (_key(source),))
