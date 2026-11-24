"""#4030 read-only remote purchase comparison, no Sync or C enrichment.

Consume the existing typed same-read purchase candidates. Only a title
is available from that contract; never infer author, right or local source.
"""
from dataclasses import dataclass
from inspect import getattr_static

from ..models import Account
from ..locking import InterProcessLock
from ..paths import acquisition_lock_file
from ..plugin_api.provider_rights_observation import ProviderRightsScope
from .canonical_recovery import _exact_borrower_bound_in_db
from .library_sqlite_freshness import sqlite_source_stamp
from .local_origin_documentary_suggestions import _fold
from .local_origin_epub_evidence import read_verified_epub_bibliography
from .local_origin_private_catalogue import _binding, _coords, _descriptive_pair
from .local_origin_remote_loans import _text, _local_observation_label
from .local_origin_suggestion_operator import LocalOriginSuggestionRequest
from .local_origin_suggestion_preview import _PublicComparisonEvidence, _verified_candidate
from .rights_snapshot_read import read_account_rights_snapshot

_MAX_CHOICES = 16
_MAX_MATCHES = 50


@dataclass(frozen=True, slots=True, repr=False)
class RemotePurchaseChoice:
    request: LocalOriginSuggestionRequest
    account: Account
    scope: ProviderRightsScope
    binding: tuple
    reader: object
    evidence: _PublicComparisonEvidence


@dataclass(frozen=True, slots=True, repr=False)
class RemotePurchaseMatch:
    branch: str
    edition_id: str
    purchase_id: str
    title: str
    observed_at: str | None
    comparison: str = "title_concordant_author_unknown"


def _reader(session):
    value = getattr_static(session, "purchase_enrichment_observation", None)
    return value if (callable(value)
                     and getattr(getattr(session, "capabilities", None),
                                 "personal_library", None) is True) else None


def _current(service, choice):
    binding = _binding(service, choice.request, choice.account, choice.scope)
    return (binding is not None and len(binding) == len(choice.binding)
            and all(a is b for a, b in zip(binding, choice.binding))
            and _reader(binding[2]) is choice.reader)


def available_remote_purchase_catalogues(service, request, *, evidence=None):
    """Inert capability discovery, including a connected uncached account."""
    try:
        if (type(request) is not LocalOriginSuggestionRequest
                or request.service is not service or request.page != 0
                or type(evidence) is not _PublicComparisonEvidence
                or not evidence.matches_scope(request) or not request.current()):
            return ()
        accounts = service.accounts
        if type(accounts) not in (tuple, list) or len(accounts) > 4096:
            return ()
        choices = []
        for account in accounts:
            if type(account) is not Account or account.identity_id != request.identity_id:
                continue
            coords = _coords(account)
            if not all(_text(x, empty=i == 3) for i, x in enumerate(coords)):
                continue
            scope = ProviderRightsScope(*coords)
            binding = _binding(service, request, account, scope)
            if binding is None or (reader := _reader(binding[2])) is None:
                continue
            choices.append(RemotePurchaseChoice(request, account, scope, binding, reader, evidence))
        selected = []
        with request.storage._lock:
            db = request.storage._connect()
            if db.in_transaction:
                return ()
            with db:
                db.execute("BEGIN")
                for choice in choices:
                    if _exact_borrower_bound_in_db(db, choice.scope):
                        selected.append(choice)
                    if len(selected) >= _MAX_CHOICES:
                        break
        return tuple(choice for choice in selected if _current(service, choice))
    except Exception:
        return ()


def compare_remote_purchase_catalogue(service, choice, *, confirmed=False,
                                      still_current=lambda: True):
    """One explicit typed account read. No locator is followed or exposed."""
    requested = False

    def report(status, matches=(), *, truncated=False, observed_at=None):
        return {"status": status, "candidates": matches,
                "window_truncated": truncated, "observed_at": observed_at,
                "provider_read_requested": requested,
                "coverage_complete": False, "fresh_network_verified": False,
                "association_authorized": False, "may_write_origin_or_right": False,
                "local_description_transmitted": False}

    if (confirmed is not True or type(choice) is not RemotePurchaseChoice
            or type(choice.request) is not LocalOriginSuggestionRequest
            or choice.request.service is not service or choice.request.page != 0
            or type(choice.evidence) is not _PublicComparisonEvidence
            or not choice.evidence.matches_scope(choice.request)
            or not callable(still_current)):
        return report("excluded")
    request = choice.request
    try:
        if still_current() is not True or not _current(service, choice):
            return report("excluded")
        with InterProcessLock(acquisition_lock_file()):
            stamp = sqlite_source_stamp(request.database)
            if stamp is None:
                return report("excluded")

            def live():
                return (still_current() is True and _current(service, choice)
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
                title, _, _ = details
                captured = []
                requested = True
                snapshot = read_account_rights_snapshot(
                    service, choice.account, cancelled=lambda: not live(),
                    purchase_candidates_sink=captured.extend,
                    require_purchase_observation=True,
                )
                if not live():
                    return report("scope_changed")
                if snapshot.scope != choice.scope or snapshot.status not in ("success", "partial"):
                    return report("unavailable")
                # The shared reader withholds candidates from unfinished
                # cursors; never follow one automatically here.
                matches = []
                for candidate in captured:
                    source = candidate.source
                    if (source.scope != choice.scope or source.kind != "purchase"
                            or not all(_text(x) for x in (source.branch, source.edition_id,
                                                         source.resource_id))
                            or _descriptive_pair(candidate.title, None) is None
                            or _fold(candidate.title) != _fold(title)):
                        continue
                    matches.append(RemotePurchaseMatch(
                        source.branch, source.edition_id, source.resource_id,
                        candidate.title, _local_observation_label(candidate.observed_at)))
                result = report("partial", tuple(matches[:_MAX_MATCHES]),
                                truncated=bool(snapshot.resume_cursor) or len(matches) > _MAX_MATCHES,
                                observed_at=_local_observation_label(snapshot.observed_at))
            return result if live() else report("scope_changed")
    except Exception:
        return report("unavailable" if requested else "excluded")
