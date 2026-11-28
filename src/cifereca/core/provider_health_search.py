"""Observe existing registered, exact-scope read operations."""
from datetime import datetime, timezone
from uuid import uuid4
from functools import wraps
from ..diagnostic_task_context import diagnostic_task_scope, diagnostic_task_reference

from .provider_health_projection import Scope, Observation, Outcome
from .notice_c_public_search_replay import _anonymous_session


def _scope(service, session, identity, capability):
    provider = getattr(session, "provider_id", None)
    identity_id = getattr(identity, "identity_id", None)
    if not provider or not identity_id or service._provider_enabled_for_identity(provider, identity_id) is not True:
        return None
    if _anonymous_session(session, provider):
        if service._sessions.get(service._session_key(provider, "__public__")) is session:
            return Scope(provider, "", "", "", capability)
        return None
    from .loan_action_scope import _inert_session_state, _inert_session_accounts
    from ..models import Account
    state = _inert_session_state(session)
    accounts = _inert_session_accounts(session)
    if state is None or accounts is None or len(accounts) != 1:
        return None
    account = accounts[0]
    if type(account) is not Account:
        return None
    credential = state.get("credential_id")
    if (state.get("connected") is not True or state.get("_closed") is True
            or credential == "__public__" or not credential
            or account.identity_id != identity_id or account.provider_id != provider
            or account.credential_id != credential
            or service._sessions.get(service._session_key(provider, credential)) is not session):
        return None
    from .capture_vault_service import capture_scope_authorizer
    from ..plugin_api.capture_vault import CaptureScope
    if not capture_scope_authorizer(service)("ui.page.diagnostics",
            CaptureScope(provider, credential, account.unique_id, identity_id)):
        return None
    return Scope(provider, credential, account.unique_id, identity_id, capability)


def _installed_version(service, provider):
    """Version of the installed plugin supplying this session, not a site version."""
    status_for = getattr(service, "_provider_plugin_status", None)
    if not callable(status_for):
        return "unknown"
    status = status_for(provider)
    descriptor = getattr(status, "descriptor", None)
    version = getattr(descriptor, "version", None)
    if (getattr(status, "state", None) != "ready"
            or getattr(status, "enabled", None) is not True
            or type(version) is not str or not version or len(version) > 200
            or not all(c.isprintable() for c in version)):
        return "unknown"
    if getattr(descriptor, "type", None) == "provider.collection":
        return "collection:" + version
    return version


def begin_public_search(service, session, identity, *, capability="search"):
    health = getattr(service, "_provider_health", None)
    if health is None:
        return None
    lease = health.lease()
    if lease is None:
        return None
    with service._source_context_lock:
        scope = _scope(service, session, identity, capability)
        if scope is None:
            return None
        return (lease, scope, diagnostic_task_reference() or ('observation:' + uuid4().hex),
                _installed_version(service, scope.provider))


def finish_public_search(service, session, identity, call, outcome, *, rows=(), business_digest=None):
    if call is None or outcome is None:
        return
    with service._source_context_lock:
        lease, scope, reference, version = call
        if (_scope(service, session, identity, scope.capability) != scope
                or _installed_version(service, scope.provider) != version):
            return
        observation = Observation(Outcome(outcome), version,
                                  datetime.now(timezone.utc).isoformat(), reference,
                                  business_digest=business_digest)
        health = service._provider_health
        health.observe(scope, observation, lease=lease)
        if scope.capability == "search" and outcome == "success":
            try:
                from ..models import SearchResult
                from ..document_classification import classification_for_search_result
                books = []
                for row in rows:
                    if (type(row) is not SearchResult or row.source_provider != scope.provider
                            or classification_for_search_result(row).meta_type not in
                            ("EBOOK", "PRINTED_BOOK")):
                        continue
                    if scope.identity:
                        if (row.source_credential != scope.instance
                                or row.source_account_id != scope.account):
                            continue
                    elif row.source_credential != "__public__" or row.source_account_id is not None:
                        continue
                    books.append((row.docbase, row.record_id))
                    if len(books) >= 32:
                        break
                health.retain_book_witnesses(scope, observation, tuple(books), lease=lease)
            except Exception:
                pass  # Witness collection cannot change search semantics.


def classify_failure(error):
    from ..search_failures import search_failure_code, search_retry_after_seconds
    code = search_failure_code(error)
    if code in ("SearchAuthenticationRequired", "SearchAccessDenied"):
        return "auth"
    if search_retry_after_seconds(error) is not None:
        return "rate_limit"
    if code == "SearchProtocolUnverified":
        return "suspected_change"
    if code == "SearchProxyUnavailable":
        return "network"
    return "internal" if code is None else "unknown"


def correlated_search(function):
    """Give each existing provider call its own worker-local trace reference."""
    @wraps(function)
    def run(*args, **kwargs):
        with diagnostic_task_scope():
            return function(*args, **kwargs)
    return run


def observe_detail(service, session, identity, action, original, accept):
    """Wrap one existing detail read, never perform admission or another read.

    The caller retains its original business validation and exception policy.
    Unchanged details are inconclusive; only accepted descriptive enrichment
    attests success. Telemetry failures cannot change the business result.
    """
    health = getattr(service, "_provider_health", None)
    if health is None or health.lease() is None:
        result = action()
        accept(result)
        return result
    with diagnostic_task_scope():
        try:
            call = begin_public_search(service, session, identity, capability="detail")
        except Exception:
            call = None

        def finish(outcome):
            try:
                finish_public_search(service, session, identity, call, outcome)
            except Exception:
                pass  # Passive monitoring must not change provider semantics.

        try:
            result = action()
        except Exception as exc:
            try:
                finish(classify_failure(exc))
            except Exception:
                pass
            raise
        # A rebind or invalid result is never credited to the old scope.
        accept(result)
        fields = ("title", "author", "publisher", "description", "thumbnail_url", "published_at")
        finish("success" if any(getattr(result, name, None) != getattr(original, name, None)
                                for name in fields) else "unknown")
        return result


def begin_account_read(service, session, account, capability):
    """Reuse exact single-account admission; no reads or navigation lookup."""
    from types import SimpleNamespace
    try:
        identity = SimpleNamespace(identity_id=account.identity_id)
        call = begin_public_search(service, session, identity, capability=capability)
        exact = Scope(account.provider_id, account.credential_id, account.unique_id,
                      account.identity_id, capability)
        return call if call is not None and call[1] == exact else None
    except Exception:
        return None


def finish_account_read(service, session, account, call, outcome, *, rows=(), loan_snapshot=None):
    if call is None or outcome is None:
        return
    from types import SimpleNamespace
    try:
        exact = Scope(account.provider_id, account.credential_id, account.unique_id,
                      account.identity_id, call[1].capability)
        if exact == call[1]:
            finish_public_search(service, session,
                                 SimpleNamespace(identity_id=account.identity_id), call, outcome, rows=rows,
                                 business_digest=_loan_digest(loan_snapshot, account))
    except Exception:
        pass


def rights_outcome(snapshot):
    """Positive evidence only; branch completeness does not attest global zero."""
    from ..plugin_api.provider_rights_observation import ProviderRightsSnapshot
    if type(snapshot) is not ProviderRightsSnapshot:
        return None
    try:
        snapshot.__post_init__()
    except (ValueError, TypeError, AttributeError):
        return "unknown"
    if snapshot.status == "cancelled":
        return None
    if (snapshot.status == "success" and snapshot.coverage == "complete"
            and snapshot.pagination_complete is True
            and any(row.provider_attested is True for row in snapshot.rights)):
        return "success"
    return "unknown"


def loan_inventory_outcome(snapshot):
    """Typed complete positive inventory, never infer a global zero."""
    from ..plugin_api.loan_inventory import LoanInventorySnapshot, LoanBranchCoverage
    if type(snapshot) is not LoanInventorySnapshot:
        return None
    try:
        if snapshot.status == "cancelled" or snapshot.reason_code in ("cancelled", "scope_changed", "unavailable"):
            return None
        if (snapshot.status == "success" and snapshot.reason_code is None
                and snapshot.observed_at and snapshot.loans and snapshot.branches
                and all(type(branch) is LoanBranchCoverage
                        and branch.completeness == "complete"
                        and branch.pagination_complete is True
                        and branch.medium != "unknown"
                        for branch in snapshot.branches)):
            return "success"
    except Exception:
        return "unknown"
    return "unknown"


def personal_library_outcome(rows, account):
    """Only accepted positive exact-source metadata; legacy empty is unknown."""
    from ..models import SearchResult
    try:
        if (type(rows) is list and rows
                and all(type(row) is SearchResult
                        and row.source_provider == account.provider_id
                        and row.source_credential == account.credential_id
                        and row.source_account_id == account.unique_id
                        and row.owned is True for row in rows)):
            return "success"
    except Exception:
        pass
    return "unknown"


def _loan_digest(snapshot, account):
    if snapshot is None:
        return None
    from .provider_health_replay import loan_snapshot_digest
    return loan_snapshot_digest(snapshot, account)
