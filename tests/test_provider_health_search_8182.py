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


def test_failure_classification():
    import httpx
    from cifereca.core.provider_health_search import classify_failure
    request = httpx.Request("GET", "https://example.invalid")
    for status, expected in [(401, "auth"), (403, "auth"), (429, "rate_limit")]:
        error = httpx.HTTPStatusError("fixture", request=request,
                                     response=httpx.Response(status, request=request))
        assert classify_failure(error) == expected


def _public_fixture():
    session = SimpleNamespace(provider_id="fixture", connected=True,
                              credential_id="__public__", credentials={}, _accounts=[])
    identity = SimpleNamespace(identity_id="viewer")
    health = PassiveHealth()
    health.set_enabled(True)
    service = SimpleNamespace(_provider_health=health, _source_context_lock=RLock(),
                              _sessions={("fixture", "__public__"): session},
                              _session_key=lambda *args: args,
                              _provider_enabled_for_identity=lambda *args: True,
                              current_identity=identity)
    return service, session, health


def test_stream_read_path(monkeypatch):
    from cifereca.core import search_orchestration as search
    service, session, health = _public_fixture()
    session.search_stream = lambda *args, **kwargs: iter(())
    monkeypatch.setattr(search, "_source_in_retry_after", lambda *args: False)
    monkeypatch.setattr(search, "consume_provider_stream", lambda *args, **kwargs:
                        SimpleNamespace(rows=[], warnings=[], error_code="", error_message=""))
    result = search._search_virtual_stream_session(
        service, session, "fixture", "all", field="all", content_types=(),
        generation=1, publish=lambda event: None, cancelled=lambda: False, control=None)
    assert result == ([], [], "", "")
    assert health.snapshot(Scope("fixture", "", "", "", "search"))[0].outcome is Outcome.UNKNOWN


def test_private_scope_refuses_multiple_accounts(monkeypatch):
    from cifereca.core import loan_action_scope, capture_vault_service
    from cifereca.models import Account
    service, session, health = _public_fixture()
    session.credential_id = "instance"
    service._sessions = {("fixture", "instance"): session}
    account = Account(provider_id="fixture", credential_id="instance",
                      unique_id="account", identity_id="viewer")
    accounts = [account]
    monkeypatch.setattr(loan_action_scope, "_inert_session_state", lambda s: vars(s))
    monkeypatch.setattr(loan_action_scope, "_inert_session_accounts", lambda s: accounts)
    monkeypatch.setattr(capture_vault_service, "capture_scope_authorizer",
                        lambda s: lambda producer, scope: True)
    call = begin_public_search(service, session, service.current_identity)
    assert call is not None
    finish_public_search(service, session, service.current_identity, call, "success")
    scope = Scope("fixture", "instance", "account", "viewer", "search")
    assert health.snapshot(scope)[0].outcome is Outcome.SUCCESS
    accounts.append(Account())
    assert begin_public_search(service, session, service.current_identity) is None
    finish_public_search(service, session, service.current_identity, call, "auth")
    assert health.snapshot(scope)[0].outcome is Outcome.SUCCESS


def test_classic_read_path(monkeypatch):
    from cifereca.core import search_orchestration as search
    from cifereca.core import notice_c_private_search_web as private
    from cifereca.core import borrow_for_identity_public_branch as public
    from cifereca.core import notice_c_private_series as series
    from cifereca.core import borrow_for_identity_native_occurrence as native
    service, session, health = _public_fixture()
    service._search_query_variants = lambda query: [query]
    calls = []
    def read(query, scope, **kwargs):
        calls.append(query)
        return []
    session.search = read
    monkeypatch.setattr(search, "_effective_search_field", lambda *a, **k: ("all", None))
    monkeypatch.setattr(search, "_source_in_retry_after", lambda *a: False)
    for module, names in [
        (private, ("capture_authenticated_search_call", "stamp_authenticated_search_rows")),
        (public, ("capture_public_branch_borrow_call", "stamp_public_branch_borrow_occurrences")),
        (series, ("capture_series_call", "stamp_series_rows")),
        (native, ("stamp_native_borrow_occurrences",)),
    ]:
        for name in names:
            monkeypatch.setattr(module, name, lambda *a, **k: None)
    assert search._search_provider_session(service, session, "fixture", "all",
                                           facade_type=object) == ([], [], "", "")
    assert calls == ["fixture"]
    assert health.snapshot(Scope("fixture", "", "", "", "search"))[0].outcome is Outcome.UNKNOWN


def test_version_is_installed_supplier_version_and_rebind_is_not_credited():
    service, session, health = _public_fixture()
    descriptor = SimpleNamespace(type="provider.library", version="2.4.1")
    service._provider_plugin_status = lambda provider: SimpleNamespace(
        descriptor=descriptor, state="ready", enabled=True)
    identity = service.current_identity
    scope = Scope("fixture", "", "", "", "search")
    call = begin_public_search(service, session, identity)
    finish_public_search(service, session, identity, call, "success")
    assert health.snapshot(scope)[0].version == "2.4.1"
    call = begin_public_search(service, session, identity)
    descriptor.version = "2.5.0"
    finish_public_search(service, session, identity, call, "auth")
    assert health.snapshot(scope)[0].outcome is Outcome.SUCCESS
    descriptor.type = "provider.collection"
    call = begin_public_search(service, session, identity)
    finish_public_search(service, session, identity, call, "success")
    assert health.snapshot(scope)[0].version == "collection:2.5.0"


def test_detail_observation_is_separate_and_requires_business_acceptance():
    import pytest
    from cifereca.core.provider_health_search import observe_detail
    service, session, health = _public_fixture()
    original = SimpleNamespace(publisher=None)
    enriched = SimpleNamespace(publisher="fixture")
    calls = []
    def action():
        calls.append("read")
        return enriched
    scope = Scope("fixture", "", "", "", "detail")
    def refuse(result):
        raise ValueError("scope changed")
    with pytest.raises(ValueError, match="scope changed"):
        observe_detail(service, session, service.current_identity, action, original, refuse)
    assert health.snapshot(scope)[0] is None
    assert observe_detail(service, session, service.current_identity, action, original,
                          lambda result: None) is enriched
    assert health.snapshot(scope)[0].outcome is Outcome.SUCCESS
    assert health.snapshot(Scope("fixture", "", "", "", "search"))[0] is None
    observe_detail(service, session, service.current_identity, lambda: original, original,
                   lambda result: None)
    assert health.snapshot(scope)[0].outcome is Outcome.UNKNOWN
    assert calls == ["read", "read"]
