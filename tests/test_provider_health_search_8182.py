from types import SimpleNamespace
from threading import RLock

from cifereca.core.provider_health_projection import PassiveHealth, Scope, Outcome
from cifereca.core.provider_health_search import begin_public_search, finish_public_search


def test_registered_public_search_and_retirement():
    session = SimpleNamespace(provider_id="fixture", connected=True,
                              credential_id="__public__", credentials={}, _accounts=[])
    identity = SimpleNamespace(identity_id="viewer")
    health = PassiveHealth()
    health.set_enabled(True)
    service = SimpleNamespace(_provider_health=health, _source_context_lock=RLock(),
                              _sessions={("fixture", "__public__"): session},
                              _session_key=lambda *args: args,
                              _provider_enabled_for_identity=lambda *args: True)
    scope = Scope("fixture", "", "", "", "search")
    call = begin_public_search(service, session, identity)
    assert call is not None
    finish_public_search(service, session, identity, call, "attested_empty")
    assert health.snapshot(scope)[0].outcome is Outcome.ATTESTED_EMPTY
    service._sessions.clear()
    finish_public_search(service, session, identity, call, "success")
    assert health.snapshot(scope)[0].outcome is Outcome.ATTESTED_EMPTY
    assert begin_public_search(service, session, identity) is None
