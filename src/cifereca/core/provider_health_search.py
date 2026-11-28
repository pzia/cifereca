"""Observe registered public and exact single-account search sessions."""
from datetime import datetime, timezone
from uuid import uuid4

from .provider_health_projection import Scope, Observation, Outcome
from .notice_c_public_search_replay import _anonymous_session


def _scope(service, session, identity):
    provider = getattr(session, "provider_id", None)
    identity_id = getattr(identity, "identity_id", None)
    if not provider or not identity_id or service._provider_enabled_for_identity(provider, identity_id) is not True:
        return None
    if _anonymous_session(session, provider):
        if service._sessions.get(service._session_key(provider, "__public__")) is session:
            return Scope(provider, "", "", "", "search")
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
    return Scope(provider, credential, account.unique_id, identity_id, "search")


def begin_public_search(service, session, identity):
    health = getattr(service, "_provider_health", None)
    if health is None:
        return None
    lease = health.lease()
    if lease is None:
        return None
    with service._source_context_lock:
        scope = _scope(service, session, identity)
        if scope is None:
            return None
        return lease, scope


def finish_public_search(service, session, identity, call, outcome):
    if call is None or outcome is None:
        return
    with service._source_context_lock:
        lease, scope = call
        if _scope(service, session, identity) != scope:
            return
        service._provider_health.observe(
            scope, Observation(Outcome(outcome), "unknown",
                               datetime.now(timezone.utc).isoformat(),
                               "observation:" + uuid4().hex), lease=lease)


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
