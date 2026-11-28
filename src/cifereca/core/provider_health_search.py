"""Observe only registered anonymous search sessions; no capture or I/O."""
from datetime import datetime, timezone
from uuid import uuid4

from .provider_health_projection import Scope, Observation, Outcome
from .notice_c_public_search_replay import _anonymous_session


def _current(service, session, identity):
    provider = getattr(session, "provider_id", None)
    identity_id = getattr(identity, "identity_id", None)
    return (bool(provider and identity_id)
            and _anonymous_session(session, provider)
            and service._sessions.get(service._session_key(provider, "__public__")) is session
            and service._provider_enabled_for_identity(provider, identity_id) is True)


def begin_public_search(service, session, identity):
    health = getattr(service, "_provider_health", None)
    if health is None:
        return None
    lease = health.lease()
    if lease is None:
        return None
    with service._source_context_lock:
        if not _current(service, session, identity):
            return None
        return lease, Scope(session.provider_id, "", "", "", "search")


def finish_public_search(service, session, identity, call, outcome):
    if call is None or outcome is None:
        return
    with service._source_context_lock:
        if not _current(service, session, identity):
            return
        lease, scope = call
        service._provider_health.observe(
            scope, Observation(Outcome(outcome), "unknown",
                               datetime.now(timezone.utc).isoformat(),
                               "observation:" + uuid4().hex), lease=lease)
