"""#4030: explicit, cache-only comparison with one exact private catalogue.

Private Core primitive, not registered as a plugin capability. No inventory
refresh, GET, URL, provider action, association or write. Descriptive equality
never attests the local file's source or the user's current legal right.
"""
from dataclasses import dataclass
from collections import Counter

from ..models import Account, Loan
from ..plugin_api.downloadable_resources import SourceReference
from ..plugin_api.provider_rights_observation import ProviderRightsScope
from .canonical_recovery import _exact_borrower_bound_in_db
from .local_origin_documentary_suggestions import _fold
from .local_origin_epub_evidence import read_verified_epub_bibliography
from .local_origin_suggestion_operator import LocalOriginSuggestionRequest
from .local_origin_suggestion_preview import _PublicComparisonEvidence, _verified_candidate
from .notice_c_purchase_descriptive_read import _read_descriptive_txn
from .public_search_projection import _public_bibliographic_text

_MAX_REFERENCES = 128
_MAX_LOANS = 4096
_MAX_MATCHES = 50
_MAX_CHOICES = 16


@dataclass(frozen=True, slots=True, repr=False)
class PrivateDocumentaryMatch:
    source: SourceReference
    title: str
    author: str | None
    comparison: str
    evidence: str
    title_observed_at: int | None


def _coords(account):
    return (account.identity_id, account.provider_id, account.credential_id, account.unique_id)


def _scope_coords(scope):
    return (scope.identity_id, scope.provider_id, scope.credential_id, scope.account_id)


def _descriptive_pair(title, author):
    if (not _public_bibliographic_text(title, required=True, limit=512)
            or not title.strip()
            or not _public_bibliographic_text(author, limit=512)):
        return None
    return title, author or None


def _loan_titles(service, scope):
    loans = getattr(service, "last_loans", ())
    if type(loans) not in (list, tuple) or len(loans) > _MAX_LOANS:
        raise ValueError("private_origin_loan_budget")
    matched = {}
    counts = Counter()
    for loan in loans:
        if (type(loan) is not Loan or loan.medium != "digital"
                or (loan.identity_id, loan.source_provider, loan.source_credential,
                    loan.source_account_id) != _scope_coords(scope)):
            continue
        key = (loan.record_id or loan.id, loan.id)
        if not all(type(value) is str and 0 < len(value) <= 256 for value in key):
            continue
        counts[key] += 1
        pair = _descriptive_pair(loan.title, loan.author)
        if pair is not None:
            matched[key] = pair
    return {key: entry for key, entry in matched.items() if counts[key] == 1}


def _snapshot(storage, scope, kind, loan_titles, title, authors):
    """Reuse exact existing C reader; do not manufacture a private Notice."""
    with storage._lock:
        db = storage._connect()
        if db.in_transaction:
            raise ValueError("private_origin_nested_transaction")
        with db:
            db.execute("BEGIN")
            if not _exact_borrower_bound_in_db(db, scope):
                raise ValueError("private_origin_account_changed")
            rows = db.execute(
                "SELECT branch_id,edition_id,right_id,evidence_ref,observed_at "
                "FROM source_references WHERE identity_id=? AND provider_id=? "
                "AND credential_id=? AND account_id=? AND reference_kind=? "
                "AND state='active' AND evidence_kind='provider_response' AND evidence_ref<>'' "
                "ORDER BY edition_id,right_id,branch_id LIMIT ?",
                (*_scope_coords(scope), kind, _MAX_REFERENCES + 1),
            ).fetchall()
            matches = []
            truncated = len(rows) > _MAX_REFERENCES
            for branch, edition, right, _, _ in rows[:_MAX_REFERENCES]:
                try:
                    source = SourceReference(scope, branch, edition, right, kind)
                except ValueError:
                    # A malformed row is not a documentary match; preserve
                    # valid neighbours in this explicitly partial window.
                    continue
                observed_at = None
                if kind == "purchase":
                    notice = _read_descriptive_txn(db, source)
                    if notice is None:
                        continue
                    titles = tuple(f for f in notice.facts if f.field == "title")
                    author_facts = tuple(f for f in notice.facts if f.field == "author")
                    if (len(titles) != 1 or len(author_facts) > 1
                            or any(f.field in ("title", "author") for f in notice.alternatives)):
                        continue
                    pair = _descriptive_pair(titles[0].value,
                                             author_facts[0].value if author_facts else None)
                    observed_at = titles[0].observed_at
                    evidence = "cached_public_purchase_description"
                else:
                    # The legacy loan projection has no branch. Reject
                    # ambiguity even if a second branch falls beyond the page.
                    count = db.execute(
                        "SELECT branch_id FROM source_references WHERE identity_id=? "
                        "AND provider_id=? AND credential_id=? AND account_id=? "
                        "AND reference_kind='loan' AND edition_id=? AND right_id=? LIMIT 2",
                        (*_scope_coords(scope), edition, right),
                    ).fetchall()
                    if count != [(branch,)]:
                        continue
                    pair = loan_titles.get((edition, right))
                    evidence = "cached_loan_description"
                if pair is None or _fold(pair[0]) != _fold(title):
                    continue
                author = pair[1]
                if len(authors) != 1 or not author:
                    comparison = "title_concordant_author_unknown"
                elif _fold(author) == _fold(authors[0]):
                    comparison = "title_author_concordant"
                else:
                    comparison = "title_concordant_author_divergent"
                if len(matches) >= _MAX_MATCHES:
                    truncated = True
                    break
                matches.append(PrivateDocumentaryMatch(
                    source, pair[0], author, comparison, evidence, observed_at,
                ))
            return tuple(rows), tuple(matches), truncated


@dataclass(frozen=True, slots=True, repr=False)
class PrivateCatalogueChoice:
    request: LocalOriginSuggestionRequest
    account: Account
    scope: ProviderRightsScope
    kind: str
    binding: tuple
    evidence: _PublicComparisonEvidence


def _binding(service, request, account, scope):
    """Inert exact account/session binding, never a provider dispatch."""
    try:
        registry, sessions = service.accounts, service._sessions
        if (type(registry) not in (list, tuple) or len(registry) > _MAX_LOANS
                or type(sessions) is not dict or len(sessions) > 256
                or not request.current() or _coords(account) != _scope_coords(scope)
                or scope.identity_id != request.identity_id or scope.credential_id == "__public__"
                or service.sync_source_enabled(account) is not True
                or not service._provider_enabled_for_identity(scope.provider_id, scope.identity_id)):
            return None
        matched = [row for row in registry if type(row) is Account
                   and _coords(row) == _scope_coords(scope)]
        selected = [s for s in sessions.values()
                    if (getattr(s, "provider_id", None), getattr(s, "credential_id", None))
                    == (scope.provider_id, scope.credential_id)]
        if len(matched) != 1 or matched[0] is not account or len(selected) != 1:
            return None
        session = selected[0]
        upstream = session.accounts
        if (getattr(session, "connected", None) is not True
                or getattr(session, "_closed", False) is not False
                or type(upstream) not in (tuple, list) or len(upstream) > _MAX_LOANS
                or sum(type(row) is Account and _coords(row) == _scope_coords(scope)
                       for row in upstream) != 1):
            return None
        return registry, sessions, session
    except Exception:
        return None


def available_known_private_catalogues(service, request, *, evidence=None):
    """Expose at most 16 explicitly selectable, connected cached surfaces.

    No inventory request and no descriptive comparison before consent.
    More accounts may exist; absence is never attested.
    """
    try:
        if (type(request) is not LocalOriginSuggestionRequest
                or request.service is not service or request.page != 0
                or type(evidence) is not _PublicComparisonEvidence
                or not evidence.matches_scope(request) or not request.current()):
            return ()
        accounts = service.accounts
        if type(accounts) not in (list, tuple) or len(accounts) > _MAX_LOANS:
            return ()
        candidates = []
        for account in accounts:
            if type(account) is not Account or account.identity_id != request.identity_id:
                continue
            try:
                scope = ProviderRightsScope(*_coords(account))
            except (ValueError, TypeError, AttributeError):
                # A malformed sibling must not erase the other connected
                # surfaces. This is a partial choice list, not coverage.
                continue
            # Do not project arbitrary account metadata or control characters.
            if not all(type(x) is str and len(x) <= 256
                       and all(ord(ch) >= 32 and ord(ch) != 127 for ch in x)
                       for x in _scope_coords(scope)):
                continue
            binding = _binding(service, request, account, scope)
            if binding is not None:
                candidates.append((account, scope, binding))
        choices = []
        with request.storage._lock:
            db = request.storage._connect()
            if db.in_transaction:
                return ()
            with db:
                db.execute("BEGIN")
                for kind in ("purchase", "loan"):
                    for account, scope, binding in candidates:
                        if not _exact_borrower_bound_in_db(db, scope):
                            continue
                        if db.execute(
                                "SELECT 1 FROM source_references WHERE identity_id=? AND provider_id=? "
                                "AND credential_id=? AND account_id=? AND reference_kind=? "
                                "AND state='active' AND evidence_kind='provider_response' "
                                "AND evidence_ref<>'' LIMIT 1", (*_scope_coords(scope), kind),
                        ).fetchone() is None:
                            continue
                        choices.append(PrivateCatalogueChoice(request, account, scope, kind, binding, evidence))
                        if len(choices) >= _MAX_CHOICES:
                            break
                    if len(choices) >= _MAX_CHOICES:
                        break
        if not request.current():
            return ()
        return tuple(choice for choice in choices if
                     (binding := _binding(service, request, choice.account, choice.scope)) is not None
                     and all(old is new for old, new in zip(choice.binding, binding)))
    except Exception:
        return ()


def compare_known_private_catalogue(service, request, *, account, kind,
                                    confirmed=False, choice=None, still_current=lambda: True):
    """One expressly selected connected account; cached claims only.

    A partial first window is not a complete inventory or a negative result.
    Only a verified unlinked local EPUB supplies the query. No query is
    transmitted to the account and no remote read is attempted.
    """
    def report(status, matches=(), more=False):
        return {"status": status, "candidates": matches, "window_truncated": more,
                "coverage_complete": False, "fresh_network_verified": False,
                "provider_access_performed": False, "association_authorized": False,
                "may_write_origin_or_right": False}

    if (confirmed is not True or type(kind) is not str or kind not in ("purchase", "loan")
            or type(request) is not LocalOriginSuggestionRequest or request.service is not service
            or request.page != 0 or request.continuation is not None
            or type(account) is not Account or not callable(still_current)):
        return report("excluded")
    try:
        scope = ProviderRightsScope(*_coords(account))
        if scope.identity_id != request.identity_id or scope.credential_id == "__public__":
            return report("excluded")
        binding = _binding(service, request, account, scope)
        if binding is None:
            return report("excluded")
        if choice is not None and (
                type(choice) is not PrivateCatalogueChoice or choice.request is not request
                or choice.account is not account or choice.scope != scope or choice.kind != kind
                or not all(old is new for old, new in zip(choice.binding, binding))
                or not choice.evidence.matches_scope(request)):
            return report("scope_changed")

        def live():
            observed = _binding(service, request, account, scope)
            return (still_current() is True and observed is not None
                    and all(old is new for old, new in zip(binding, observed)))

        if not live():
            return report("excluded")
        with _verified_candidate(request.storage, library_root=request.root,
                                 identity_id=request.identity_id,
                                 placement_id=request.placement_id,
                                 catalogue=True) as (local, path, signature):
            if choice is not None and (choice.evidence.evidence != local
                                       or choice.evidence.signature != signature):
                return report("scope_changed")
            if local[6] not in ("epub", "kepub"):
                return report("excluded")
            description = read_verified_epub_bibliography(path, signature)
            if description is None or not live():
                return report("excluded")
            title, authors, _isbns = description
            loan_titles = _loan_titles(service, scope) if kind == "loan" else {}
            first = _snapshot(request.storage, scope, kind, loan_titles, title, authors)
            if not live() or (kind == "loan" and _loan_titles(service, scope) != loan_titles):
                return report("scope_changed")
            if _snapshot(request.storage, scope, kind, loan_titles, title, authors) != first:
                return report("scope_changed")
            result = report("partial", first[1], first[2])
        return result if live() else report("scope_changed")
    except Exception:
        return report("unavailable")
