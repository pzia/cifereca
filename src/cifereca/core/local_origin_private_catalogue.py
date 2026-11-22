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
from .library_sqlite_freshness import sqlite_source_stamp
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
_MAX_PAGES = 32


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
        if (type(loan) is not Loan
                or (loan.identity_id, loan.source_provider, loan.source_credential,
                    loan.source_account_id) != _scope_coords(scope)):
            continue
        # Count the exact account's occurrence BEFORE medium, edition or
        # descriptive filters; a contradictory sibling cannot disappear
        # from the ambiguity check. Other accounts remain independent.
        if type(loan.id) is str and 0 < len(loan.id) <= 256:
            counts[loan.id] += 1
        if loan.medium != "digital":
            continue
        key = (loan.record_id or loan.id, loan.id)
        if not all(type(value) is str and 0 < len(value) <= 256 for value in key):
            continue
        pair = _descriptive_pair(loan.title, loan.author)
        if pair is not None:
            matched[key] = pair
    return {key: entry for key, entry in matched.items() if counts[key[1]] == 1}


def _purchase_description(notice, title):
    """Select a matching literal *for presentation*, not a canonical value.

    Retained catalogue/detail alternatives for this exact purchase can
    disagree. Expose a candidate with a distinct ambiguity reason rather
    than silently discard it or select a winning author. Only safe decoded
    text is returned; other fields and all acquisition authority stay out.
    """
    title_facts = tuple(f for f in notice.facts + notice.alternatives
                        if f.field == "title")
    author_facts = tuple(f for f in notice.facts + notice.alternatives
                         if f.field == "author")
    if (not 1 <= len(title_facts) <= 16 or len(author_facts) > 16
            or any(_descriptive_pair(f.value, None) is None for f in title_facts)
            or any(type(f.value) is not str or not _public_bibliographic_text(
                f.value, required=True, limit=512) or not f.value.strip()
                   for f in author_facts)):
        return None
    matching = tuple(f for f in title_facts if _fold(f.value) == _fold(title))
    if not matching:
        return None
    title_values = {f.value for f in title_facts}
    author_values = {f.value for f in author_facts}
    conflicting = len(title_values) > 1 or len(author_values) > 1
    # Do not select a winning author from conflicting declarations. The
    # displayed title is one matching observation, never a consolidated title.
    # Its date belongs to that observation, not the receipt's refresh time.
    author = next(iter(author_values)) if len(author_values) == 1 else None
    selected = matching[0]
    pair = _descriptive_pair(selected.value, author)
    return (pair, selected.observed_at, conflicting) if pair is not None else None


def _snapshot(storage, scope, kind, loan_titles, title, authors, offset=0):
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
                "ORDER BY edition_id,right_id,branch_id LIMIT ? OFFSET ?",
                (*_scope_coords(scope), kind, _MAX_REFERENCES + 1, offset),
            ).fetchall()
            matches = []
            truncated = len(rows) > _MAX_REFERENCES
            consumed = min(len(rows), _MAX_REFERENCES)
            for index, (branch, edition, right, _, _) in enumerate(rows[:_MAX_REFERENCES]):
                try:
                    source = SourceReference(scope, branch, edition, right, kind)
                except ValueError:
                    # A malformed row is not a documentary match; preserve
                    # valid neighbours in this explicitly partial window.
                    continue
                observed_at = None
                conflicting = False
                if kind == "purchase":
                    notice = _read_descriptive_txn(db, source)
                    if notice is None:
                        continue
                    observed = _purchase_description(notice, title)
                    if observed is None:
                        continue
                    pair, observed_at, conflicting = observed
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
                if conflicting:
                    comparison = "purchase_description_conflicting"
                elif len(authors) != 1 or not author:
                    comparison = "title_concordant_author_unknown"
                elif _fold(author) == _fold(authors[0]):
                    comparison = "title_author_concordant"
                else:
                    comparison = "title_concordant_author_divergent"
                if len(matches) >= _MAX_MATCHES:
                    truncated = True
                    # This additional matching row has not been delivered.
                    # Resume AT it rather than skipping the rest of the window.
                    consumed = index
                    break
                matches.append(PrivateDocumentaryMatch(
                    source, pair[0], author, comparison, evidence, observed_at,
                ))
            # Rank only this explicitly bounded cache window; the next page
            # is independently checked and no global inventory is claimed.
            # Stable ties keep exact source-row order for human comparison.
            evidence_rank = {
                "title_author_concordant": 0,
                "title_concordant_author_unknown": 1,
                "title_concordant_author_divergent": 2,
                "purchase_description_conflicting": 3,
            }
            ordered = tuple(sorted(
                matches, key=lambda row: evidence_rank.get(row.comparison, 4)))
            return tuple(rows), ordered, truncated, offset + consumed


@dataclass(frozen=True, slots=True, repr=False)
class PrivateCatalogueChoice:
    request: LocalOriginSuggestionRequest
    account: Account
    scope: ProviderRightsScope
    kind: str
    binding: tuple
    evidence: _PublicComparisonEvidence


@dataclass(frozen=True, slots=True, repr=False)
class PrivateCatalogueContinuation:
    """Private, process-local continuation, never a provider or right receipt."""
    request: LocalOriginSuggestionRequest
    account: Account
    scope: ProviderRightsScope
    kind: str
    binding: tuple
    evidence: _PublicComparisonEvidence
    database_stamp: tuple
    offset: int
    page: int
    loan_titles: tuple


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
                                    confirmed=False, choice=None, still_current=lambda: True,
                                    continuation=None):
    """One expressly selected connected account; cached claims only.

    A partial window is not a complete inventory or a negative result.
    Further explicit pages require the same file, scope and SQLite image.
    Only a verified unlinked local EPUB supplies the query. No query is
    transmitted to the account and no remote read is attempted.
    """
    def report(status, matches=(), more=False, following=None):
        return {"status": status, "candidates": matches, "window_truncated": more,
                "next_cursor": following,
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
        stamp = sqlite_source_stamp(request.database)
        if stamp is None:
            return report("excluded")
        offset, page = 0, 1
        if continuation is not None:
            if (type(continuation) is not PrivateCatalogueContinuation
                    or continuation.request is not request
                    or continuation.account is not account
                    or continuation.scope != scope or continuation.kind != kind
                    or len(continuation.binding) != len(binding)
                    or not all(old is new for old, new in zip(continuation.binding, binding))
                    or type(continuation.evidence) is not _PublicComparisonEvidence
                    or not continuation.evidence.matches_scope(request)
                    or continuation.database_stamp != stamp
                    or type(continuation.page) is not int
                    or not 2 <= continuation.page <= _MAX_PAGES
                    or type(continuation.offset) is not int
                    or not 0 < continuation.offset <= _MAX_REFERENCES * (continuation.page - 1)):
                return report("scope_changed")
            offset, page = continuation.offset, continuation.page
        with _verified_candidate(request.storage, library_root=request.root,
                                 identity_id=request.identity_id,
                                 placement_id=request.placement_id,
                                 catalogue=True) as (local, path, signature):
            if choice is not None and (choice.evidence.evidence != local
                                       or choice.evidence.signature != signature):
                return report("scope_changed")
            if continuation is not None and (
                    continuation.evidence.evidence != local
                    or continuation.evidence.signature != signature):
                return report("scope_changed")
            if local[6] not in ("epub", "kepub"):
                return report("excluded")
            description = read_verified_epub_bibliography(path, signature)
            if description is None or not live():
                return report("excluded")
            title, authors, _isbns = description
            loan_titles = _loan_titles(service, scope) if kind == "loan" else {}
            loan_witness = tuple(sorted(loan_titles.items()))
            if continuation is not None and continuation.loan_titles != loan_witness:
                return report("scope_changed")
            first = _snapshot(request.storage, scope, kind, loan_titles, title, authors, offset)
            if (not live() or sqlite_source_stamp(request.database) != stamp
                    or (kind == "loan" and _loan_titles(service, scope) != loan_titles)):
                return report("scope_changed")
            if _snapshot(request.storage, scope, kind, loan_titles, title, authors, offset) != first:
                return report("scope_changed")
            following = None
            if first[2] and page < _MAX_PAGES and first[3] > offset:
                evidence = _PublicComparisonEvidence(
                    request.storage, request.root, request.identity_id,
                    request.placement_id, local, signature,
                )
                following = PrivateCatalogueContinuation(
                    request, account, scope, kind, binding, evidence,
                    stamp, first[3], page + 1, loan_witness,
                )
            result = report("partial", first[1], first[2], following)
        return result if live() and sqlite_source_stamp(request.database) == stamp else report("scope_changed")
    except Exception:
        return report("unavailable")
