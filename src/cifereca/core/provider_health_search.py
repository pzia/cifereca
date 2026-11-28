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


def finish_public_search(service, session, identity, call, outcome):
    if call is None or outcome is None:
        return
    with service._source_context_lock:
        lease, scope, reference, version = call
        if (_scope(service, session, identity, scope.capability) != scope
                or _installed_version(service, scope.provider) != version):
            return
        service._provider_health.observe(
            scope, Observation(Outcome(outcome), version,
                               datetime.now(timezone.utc).isoformat(),
                               reference), lease=lease)


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
