"""#4030: explicit, scoped private loan inventory comparison.

Opt-in typed inventory only. The local title never crosses the provider
boundary. No persistence, sync, download, alias inference or association.
The optional inventory snapshot has no exact canonical branch per loan;
remote comparisons therefore cannot be consumed as SourceReferences.
"""
from collections import Counter
from dataclasses import dataclass
from inspect import getattr_static
from unicodedata import category

from ..locking import InterProcessLock
from ..models import Account, Loan
from ..paths import acquisition_lock_file
from ..plugin_api.loan_inventory import LoanInventoryScope, read_loan_inventory
from ..plugin_api.provider_rights_observation import ProviderRightsScope
from .canonical_recovery import _exact_borrower_bound_in_db
from .library_sqlite_freshness import sqlite_source_stamp
from .local_origin_documentary_suggestions import _fold
from .local_origin_epub_evidence import read_verified_epub_bibliography
from .local_origin_private_catalogue import _binding, _coords, _descriptive_pair
from .local_origin_suggestion_operator import LocalOriginSuggestionRequest
from .local_origin_suggestion_preview import _PublicComparisonEvidence, _verified_candidate

_MAX_ACCOUNTS = 4096
_MAX_CHOICES = 16
_MAX_LOANS = 4096
_MAX_MATCHES = 50


@dataclass(frozen=True, slots=True, repr=False)
class RemoteLoanCatalogueChoice:
    request: LocalOriginSuggestionRequest
    account: Account
    scope: ProviderRightsScope
    binding: tuple
    reader: object
    evidence: _PublicComparisonEvidence


@dataclass(frozen=True, slots=True, repr=False)
class RemoteLoanDocumentaryMatch:
    edition_id: str | None
    loan_id: str
    title: str
    author: str | None
    comparison: str


def _text(value, *, empty=False):
    # IDs (account/provider, edition and loan occurrence) are rendered by
    # the local review; supplier responses may contain bidi/C1 controls.
    return (type(value) is str and (empty or bool(value)) and len(value) <= 256
            and all(category(ch) not in ("Cc", "Cf", "Cs", "Zl", "Zp")
                    for ch in value))


def _reader(session):
    reader = getattr_static(session, "loan_inventory_snapshot", None)
    if (not callable(reader)
            or getattr(getattr(session, "capabilities", None), "loans", None) is not True):
        return None
    return reader


def _choice_current(service, choice):
    binding = _binding(service, choice.request, choice.account, choice.scope)
    return (binding is not None and len(binding) == len(choice.binding)
            and all(a is b for a, b in zip(binding, choice.binding))
            and _reader(binding[2]) is choice.reader)


def available_remote_loan_catalogues(service, request, *, evidence=None):
    """Inert choice discovery, including accounts with no retained loans.

    A choice only names an explicitly selectable surface. No inventory read,
    local title transmission, presence or right is established by discovery.
    """
    try:
        if (type(request) is not LocalOriginSuggestionRequest
                or request.service is not service or request.page != 0
                or type(evidence) is not _PublicComparisonEvidence
                or not evidence.matches_scope(request) or not request.current()):
            return ()
        accounts = service.accounts
        if type(accounts) not in (list, tuple) or len(accounts) > _MAX_ACCOUNTS:
            return ()
        candidates = []
        for account in accounts:
            if type(account) is not Account or account.identity_id != request.identity_id:
                continue
            coords = _coords(account)
            if not all(_text(x, empty=i == 3) for i, x in enumerate(coords)):
                continue
            scope = ProviderRightsScope(*coords)
            binding = _binding(service, request, account, scope)
            if binding is None:
                continue
            reader = _reader(binding[2])
            if reader is not None:
                candidates.append(RemoteLoanCatalogueChoice(request, account, scope,
                                                            binding, reader, evidence))
        choices = []
        with request.storage._lock:
            db = request.storage._connect()
            if db.in_transaction:
                return ()
            with db:
                db.execute("BEGIN")
                for choice in candidates:
                    if _exact_borrower_bound_in_db(db, choice.scope):
                        choices.append(choice)
                    if len(choices) >= _MAX_CHOICES:
                        break
        return tuple(c for c in choices if _choice_current(service, c))
    except Exception:
        return ()


def _matches(snapshot, choice, title, authors):
    if len(snapshot.loans) > _MAX_LOANS:
        return (), True
    counts, candidates = Counter(), []
    for loan in snapshot.loans:
        if (type(loan) is not Loan
                or (loan.identity_id, loan.source_provider, loan.source_credential,
                    loan.source_account_id) != _coords(choice.account)
                or not _text(loan.id)):
            continue
        # Count every occurrence in this exact scope BEFORE filtering the
        # descriptive fields or medium. A malformed or physical twin cannot
        # silently qualify the surviving digital occurrence as unique.
        counts[loan.id] += 1
        if (loan.medium != "digital"
                or (loan.record_id not in (None, "") and not _text(loan.record_id))):
            continue
        # Do not replace a missing edition by the occurrence ID. The provider
        # did not report that relationship, nor a canonical branch.
        key = (loan.record_id or None, loan.id)
        pair = _descriptive_pair(loan.title, loan.author)
        if pair is None or _fold(pair[0]) != _fold(title):
            continue
        author = pair[1]
        comparison = ("title_concordant_author_unknown" if len(authors) != 1 or not author
                      else "title_author_concordant" if _fold(author) == _fold(authors[0])
                      else "title_concordant_author_divergent")
        candidates.append(RemoteLoanDocumentaryMatch(*key, pair[0], author, comparison))
    unique = tuple(c for c in candidates if counts[c.loan_id] == 1)
    # Rank this bounded typed-inventory window by documentary evidence,
    # never by provider order or a claimed right. Stable ties preserve exact
    # occurrence order; no branch, ownership or acquisition is inferred.
    evidence_rank = {
        "title_author_concordant": 0,
        "title_concordant_author_unknown": 1,
        "title_concordant_author_divergent": 2,
    }
    ordered = tuple(sorted(
        unique, key=lambda row: evidence_rank.get(row.comparison, 3),
    ))
    return ordered[:_MAX_MATCHES], len(ordered) > _MAX_MATCHES


def compare_remote_loan_catalogue(service, choice, *, confirmed=False,
                                  still_current=lambda: True):
    """Read ONE explicitly chosen typed inventory under the acquisition lock.

    Only the exact Account is passed to the supplier. No discovery of
    other accounts, legacy fallback, title/ISBN transmission or library
    refresh. A supplier's cache may satisfy the typed read; transport
    freshness and coverage are not claimed. No output can authorize A/B.
    """
    requested = False

    def report(status, candidates=(), truncated=False, observed_at=None):
        return {"status": status, "candidates": candidates,
                "window_truncated": truncated, "observed_at": observed_at,
                "provider_read_requested": requested,
                "coverage_complete": False, "fresh_network_verified": False,
                "association_authorized": False, "may_write_origin_or_right": False,
                "local_description_transmitted": False}

    if (confirmed is not True or type(choice) is not RemoteLoanCatalogueChoice
            or type(choice.request) is not LocalOriginSuggestionRequest
            or choice.request.service is not service or choice.request.page != 0
            or type(choice.account) is not Account
            or type(choice.scope) is not ProviderRightsScope
            or type(choice.evidence) is not _PublicComparisonEvidence
            or not choice.evidence.matches_scope(choice.request)
            or not callable(still_current)):
        return report("excluded")
    request = choice.request
    try:
        if still_current() is not True or not _choice_current(service, choice):
            return report("excluded")
        with InterProcessLock(acquisition_lock_file()):
            stamp = sqlite_source_stamp(request.database)
            if stamp is None:
                return report("excluded")

            def live():
                return (still_current() is True and _choice_current(service, choice)
                        and sqlite_source_stamp(request.database) == stamp
                        and request.storage.provider_scope_account_bound(
                            identity_id=choice.scope.identity_id,
                            provider_id=choice.scope.provider_id,
                            credential_id=choice.scope.credential_id,
                            account_id=choice.scope.account_id) is True)

            with _verified_candidate(request.storage, library_root=request.root,
                                     identity_id=request.identity_id,
                                     placement_id=request.placement_id,
                                     catalogue=True) as (local, path, signature):
                if (local != choice.evidence.evidence or signature != choice.evidence.signature
                        or not live() or local[6] not in ("epub", "kepub")):
                    return report("scope_changed")
                details = read_verified_epub_bibliography(path, signature)
                if details is None or not live():
                    return report("excluded")
                title, authors, _ = details
                requested = True
                snapshot = read_loan_inventory(choice.binding[2], choice.account,
                                               scope_current=live, typed_only=True)
                if not live():
                    return report("scope_changed")
                if (snapshot.scope != LoanInventoryScope.for_account(choice.account)
                        or snapshot.status not in ("success", "partial")):
                    return report("unavailable")
                candidates, truncated = _matches(snapshot, choice, title, authors)
                result = report("partial", candidates, truncated, snapshot.observed_at)
            return result if live() else report("scope_changed")
    except Exception:
        return report("unavailable" if requested else "excluded")
