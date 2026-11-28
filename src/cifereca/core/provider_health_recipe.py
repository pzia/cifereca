"""Explicit sequential health recipe using already admitted inventory readers.

No synchronization, account connection, credential creation, acquisition,
delivery or fallback. Unsupported steps are reported as unknown. Only a
currently selected identity with an exact registered account can execute.
"""
from inspect import getattr_static
from time import time
from ..plugin_api.lending_borrower_eligibility import BorrowerEligibilitySnapshot
from ..plugin_api.lending_capabilities import BorrowerOperationEvidence

from ..models import Account, SearchResult, ItemDetail, Holding
from ..plugin_api import ProviderSearchResponse, Cancelled
from ..providers.base import ProviderDescriptor
from .private_inventory_row_scope import declared_private_inventory_batch_valid
from .provider_explicit_enrichment import admit_provider_detail_enrichment, can_enrich_provider_detail_result
from ..plugin_api.capture_vault import CaptureScope
from ..plugin_api.loan_inventory import read_loan_inventory
from ..diagnostic_task_context import diagnostic_task_scope
from ..http_trace import HTTP_TRACE
from ..locking import InterProcessLock, LockBusyError
from ..paths import acquisition_lock_file
from .provider_health_inventory import _diagnostic_generation
from .provider_health_search import (begin_account_read, finish_account_read, loan_inventory_outcome,
                                     rights_outcome, personal_library_outcome, classify_failure)
from .rights_snapshot_read import read_account_rights_snapshot
from .private_inventory_binding import private_inventory_binding_current
from .loan_action_scope import _inert_session_state, _inert_session_accounts
from .capture_vault_service import capture_scope_authorizer

_INITIAL_QUERY = "livre"

_STEPS = ("search", "detail", "pagination", "availability", "personal_library",
          "loans", "quotas", "rights")


def _scope(account):
    return CaptureScope(account.provider_id, account.credential_id,
                        account.unique_id, account.identity_id)


def _identity(service):
    return getattr(getattr(service, "current_identity", None), "identity_id", None)


def recipe_options(service):
    """Metadata-only list; no provider getter or live connection is opened."""
    try:
        health = service._provider_health
        generation = _diagnostic_generation(service, health)
        identity = _identity(service)
        if generation is None or not identity:
            return ()
        authorize = capture_scope_authorizer(service)
        result = []
        for account in service.accounts_for_identity(identity):
            if type(account) is not Account:
                continue
            scope = _scope(account)
            if (not all(type(v) is str and v for v in (
                    scope.provider_id, scope.instance_id, scope.account_id, scope.identity_id))
                    or scope.identity_id != identity
                    or not authorize("ui.page.diagnostics", scope)):
                continue
            result.append({"scope": scope, "generation": generation,
                           "enabled": service._provider_enabled_for_identity(
                               scope.provider_id, identity) is True})
        if _identity(service) != identity or _diagnostic_generation(service, health) != generation:
            return ()
        return tuple(result[:256])
    except Exception:
        return ()


def _exact_recipe_rows(rows, scope):
    """No private record or account may be borrowed from another scope."""
    return (type(rows) is list and len(rows) <= 10000
            and all(type(row) is SearchResult
                    and row.source_provider == scope.provider_id
                    and row.source_credential == scope.instance_id
                    and row.source_account_id == scope.account_id
                    and type(row.record_id) is str and bool(row.record_id)
                    and type(row.docbase) is str and bool(row.docbase)
                    for row in rows))


def _qualified_search_outcome(response, scope):
    # The current terminal_empty contract attests anonymous responses only.
    # An empty authenticated search is unknown, even if a legacy flag is set.
    if (type(response) is not ProviderSearchResponse
            or response.partial is not False
            or not _exact_recipe_rows(response.results, scope)):
        return "unknown", ()
    return ("success", tuple(response.results)) if response.results else ("unknown", ())


def _availability_outcome(detail, item):
    """Positive exact holding evidence only; no inferred quota or global zero."""
    if (type(detail) is not ItemDetail
            or (detail.record_id, detail.docbase, detail.source_provider, detail.source_credential)
            != (item.record_id, item.docbase, item.source_provider, item.source_credential)
            or type(detail.holdings) is not list or not 1 <= len(detail.holdings) <= 10000
            or not all(type(row) is Holding and type(row.is_available) is bool
                       for row in detail.holdings)):
        return "unknown"
    return ("success" if detail.availability_status == "available"
            and any(row.is_available is True for row in detail.holdings) else "unknown")


def _quota_read_outcome(snapshot, previous, scope, epoch, started, branches, *, now):
    """Only newly published typed account facts from existing recipe reads.

    Zero remaining loans is a successful quota observation, not permission
    to borrow. Never use a stale snapshot, local preference or stock count.
    """
    if (type(snapshot) is not BorrowerEligibilitySnapshot or snapshot is previous
            or type(epoch) not in (int, str)
            or type(snapshot.session_epoch) is not type(epoch)
            or snapshot.session_epoch != epoch
            or type(snapshot.observed_at) is not int
            or not started <= snapshot.observed_at <= now
            or now - snapshot.observed_at > 300
            or type(snapshot.account_evidence) is not tuple
            or len(snapshot.account_evidence) > 512
            or not branches):
        return "unknown", "quota_not_attested"
    values = []
    for branch in branches:
        if type(branch) is not str or not branch:
            return "unknown", "quota_not_attested"
        matches = [row for row in snapshot.account_evidence
                   if type(row) is BorrowerOperationEvidence
                   and (row.identity_id, row.provider_id, row.credential_id,
                        row.account_id, row.branch, row.operation) ==
                       (scope.identity_id, scope.provider_id, scope.instance_id,
                        scope.account_id, branch, "loan.borrow")]
        if (len(matches) != 1 or type(matches[0].remaining_loans) is not int
                or matches[0].remaining_loans < 0):
            return "unknown", "quota_not_attested"
        values.append(matches[0].remaining_loans)
    return "success", ("quota_exhausted" if all(value == 0 for value in values)
                       else "quota_observed")


def run_recipe(service, scope, generation, *, confirmed=False, control=None):
    """One explicit account; all steps reported, unsupported never success."""
    def report(status, reason):
        return tuple({"capability": name, "outcome": status, "reason": reason}
                     for name in _STEPS)

    if confirmed is not True:
        return report("unknown", "confirmation_required")
    if type(scope) is not CaptureScope or type(generation) is not tuple:
        return report("unknown", "scope_denied")

    try:
        health = service._provider_health
        if not any(row["scope"] == scope and row["generation"] == generation
                   and row["enabled"] for row in recipe_options(service)):
            return report("unknown", "scope_denied")
        matches = [row for row in service.accounts
                   if type(row) is Account and _scope(row) == scope]
        if len(matches) != 1:
            return report("unknown", "scope_denied")
        account = matches[0]
        session = service._sessions.get(service._session_key(scope.provider_id, scope.instance_id))
        state = _inert_session_state(session)
        upstream = _inert_session_accounts(session)
        if state is None or upstream is None or len(upstream) != 1 or upstream[0] is not account:
            return report("unknown", "unqualified_session")
        descriptor = state.get("descriptor")
        if type(descriptor) is not ProviderDescriptor or descriptor.id != scope.provider_id:
            return report("unknown", "unqualified_descriptor")
        session_epoch = state.get("_epoch")
        coordinates = (scope.identity_id, scope.provider_id, scope.instance_id, scope.account_id)

        def current():
            try:
                current_state = _inert_session_state(session)
                return (not (control is not None and control.cancelled())
                        and _identity(service) == scope.identity_id
                        and service._provider_health is health
                        and _diagnostic_generation(service, health) == generation
                        and HTTP_TRACE.status()["active"] is True
                        and current_state is not None
                        and current_state.get("_epoch") == session_epoch
                        and current_state.get("descriptor") is descriptor
                        and service._provider_enabled_for_identity(scope.provider_id, scope.identity_id) is True
                        and (upstream_now := _inert_session_accounts(session)) is not None
                        and len(upstream_now) == 1 and upstream_now[0] is account
                        and private_inventory_binding_current(service, account, session,
                                                             scope=coordinates, epoch=generation[0]))
            except Exception:
                return False

        results = {name: {"capability": name, "outcome": "unknown",
                          "reason": "reader_not_qualified"} for name in _STEPS}
        def put(name, outcome, reason):
            results[name] = {"capability": name, "outcome": outcome or "unknown", "reason": reason}

        with InterProcessLock(acquisition_lock_file()):
            if not current():
                return report("unknown", "scope_or_trace_unavailable")
            # One explicit query through the public optional typed ABI.
            # Do not retry, switch identity, authenticate or synthesize pages.
            caps = descriptor.capabilities
            search_caps = descriptor.search_capabilities
            search_rows = ()
            quota_previous = _inert_session_state(session).get("borrower_eligibility_snapshot")
            quota_started = int(time())
            if (caps.search is True and search_caps.supports_field("all")
                    and search_caps.supports_scope("all")
                    and callable(getattr_static(session, "search_with_response_facets", None))):
                with diagnostic_task_scope():
                    call = begin_account_read(service, session, account, "search")
                    if call is not None and current():
                        try:
                            response = session.search_with_response_facets(
                                _INITIAL_QUERY, "all", field="all", content_types=())
                            outcome, rows = _qualified_search_outcome(response, scope)
                        except Cancelled:
                            return report("unknown", "cancelled")
                        except Exception as exc:
                            outcome, rows = classify_failure(exc), ()
                        if not current():
                            return report("unknown", "scope_or_trace_unavailable")
                        finish_account_read(service, session, account, call, outcome, rows=rows)
                        put("search", outcome, "typed_search")
                        if outcome == "success":
                            search_rows = rows
                        # A single bounded second page, only after a positive
                        # first page and an explicit page-index contract.
                        if (outcome == "success" and response.catalogue_complete is False
                                and search_caps.pagination == "page"
                                and type(search_caps.max_pages) is int
                                and search_caps.max_pages >= 2
                                and current()):
                            with diagnostic_task_scope():
                                page_call = begin_account_read(service, session, account, "pagination")
                                if page_call is None or not current():
                                    return report("unknown", "scope_or_trace_unavailable")
                                try:
                                    page_rows = session.search(_INITIAL_QUERY, "all", page=1, field="all")
                                    first = {(row.docbase, row.record_id) for row in rows}
                                    valid = (_exact_recipe_rows(page_rows, scope)
                                             and any((row.docbase, row.record_id) not in first
                                                     for row in page_rows))
                                    if not current():
                                        return report("unknown", "scope_or_trace_unavailable")
                                    page_outcome = "success" if valid else "unknown"
                                    finish_account_read(service, session, account, page_call, page_outcome)
                                    put("pagination", page_outcome, "bounded_second_page")
                                except Cancelled:
                                    return report("unknown", "cancelled")
                                except Exception as exc:
                                    if not current():
                                        return report("unknown", "scope_or_trace_unavailable")
                                    page_outcome = classify_failure(exc)
                                    finish_account_read(service, session, account, page_call, page_outcome)
                                    put("pagination", page_outcome, "page_unavailable")

            if not current():
                return report("unknown", "scope_or_trace_unavailable")
            # The declared holdings ABI is read-only. Work on a copy because
            # some historical implementations enrich their input DTO in place.
            if caps.holdings is True and search_rows:
                from copy import deepcopy
                with diagnostic_task_scope():
                    call = begin_account_read(service, session, account, "availability")
                    if call is not None and current():
                        candidate = search_rows[0]
                        try:
                            detail = session.holdings(deepcopy(candidate), search_terms=_INITIAL_QUERY)
                            outcome = _availability_outcome(detail, candidate)
                        except Cancelled:
                            return report("unknown", "cancelled")
                        except Exception as exc:
                            outcome = classify_failure(exc)
                        if not current():
                            return report("unknown", "scope_or_trace_unavailable")
                        finish_account_read(service, session, account, call, outcome)
                        put("availability", outcome, "declared_holdings")
            if not current():
                return report("unknown", "scope_or_trace_unavailable")
            # Existing read ABI; never mirror into the catalogue or fetch a file.
            library_rows = ()
            if caps.personal_library is True and callable(getattr_static(session, "library", None)):
                with diagnostic_task_scope():
                    call = begin_account_read(service, session, account, "personal_library")
                    if call is not None and current():
                        try:
                            rows = session.library()
                            valid = (_exact_recipe_rows(rows, scope)
                                     and declared_private_inventory_batch_valid(
                                         rows, session=session, provider_id=scope.provider_id,
                                         credential_id=scope.instance_id))
                            outcome = personal_library_outcome(rows, account) if valid else "unknown"
                            if outcome == "success":
                                library_rows = tuple(rows)
                        except Cancelled:
                            return report("unknown", "cancelled")
                        except Exception as exc:
                            outcome = classify_failure(exc)
                        if not current():
                            return report("unknown", "scope_or_trace_unavailable")
                        finish_account_read(service, session, account, call, outcome)
                        put("personal_library", outcome, "private_inventory")
            if not current():
                return report("unknown", "scope_or_trace_unavailable")
            # Reuse the explicit descriptive-only enrichment admission.
            candidate = next((row for row in library_rows
                              if can_enrich_provider_detail_result(row)), None)
            if candidate is not None and callable(getattr_static(session, "enrich_owned_public_detail", None)):
                try:
                    work = admit_provider_detail_enrichment(
                        service, candidate, identity_id=scope.identity_id, user_confirmed=True)
                    if not current():
                        return report("unknown", "scope_or_trace_unavailable")
                    result = work()
                    if not current():
                        return report("unknown", "scope_or_trace_unavailable")
                    fields = ("title", "author", "publisher", "description", "thumbnail_url", "published_at")
                    changed = any(getattr(result, name) != getattr(candidate, name) for name in fields)
                    put("detail", "success" if changed else "unknown", "descriptive_enrichment")
                except Exception:
                    put("detail", "unknown", "detail_unavailable")
            if not current():
                return report("unknown", "scope_or_trace_unavailable")
            # Deliberately typed-only: a list-only legacy fallback does not
            # establish coverage and is not admitted for this explicit recipe.
            if (getattr(getattr(descriptor, "capabilities", None), "loans", None) is True
                    and callable(getattr_static(session, "loan_inventory_snapshot", None))):
                with diagnostic_task_scope():
                    call = begin_account_read(service, session, account, "loans")
                    if call is not None and current():
                        snapshot = read_loan_inventory(session, account, descriptor=descriptor,
                                                       scope_current=current, typed_only=True)
                        if not current():
                            return report("unknown", "scope_or_trace_unavailable")
                        outcome = loan_inventory_outcome(snapshot)
                        finish_account_read(service, session, account, call, outcome, loan_snapshot=snapshot)
                        put("loans", outcome, "typed_inventory")
            if not current():
                return report("unknown", "scope_or_trace_unavailable")
            if any(callable(getattr_static(session, name, None)) for name in (
                    "downloadable_resource_snapshot", "rights_observation_snapshot")):
                snapshot = read_account_rights_snapshot(service, account, cancelled=lambda: not current())
                if not current():
                    return report("unknown", "scope_or_trace_unavailable")
                put("rights", rights_outcome(snapshot), "typed_rights")
            if not current():
                return report("unknown", "scope_or_trace_unavailable")
            # No additional call: only inspect a fresh publication made by
            # one of the already executed reads. Do not invent a trace link.
            state_now = _inert_session_state(session)
            quota_snapshot = state_now.get("borrower_eligibility_snapshot")
            branches = frozenset(row.docbase for row in (*search_rows, *library_rows))
            quota_outcome, quota_reason = _quota_read_outcome(
                quota_snapshot, quota_previous, scope, session_epoch, quota_started,
                branches, now=int(time()))
            if not current() or state_now.get("borrower_eligibility_snapshot") is not quota_snapshot:
                return report("unknown", "scope_or_trace_unavailable")
            put("quotas", quota_outcome, quota_reason)
            return tuple(results[name] for name in _STEPS)
    except LockBusyError:
        return report("unknown", "busy")
    except Exception:
        return report("unknown", "recipe_unavailable")
