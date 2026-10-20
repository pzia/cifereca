"""#1916: cached results cannot cross configured accounts for one provider ID."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from cifereca.core.search_orchestration import _search_session_cache_scope
from tests.test_core043_progress_cache_stats import _FakeSession, _service


def _account_session(credential_id, *, name, calls):
    session = _FakeSession("one_provider")
    session.credential_id = credential_id
    original = session.search

    def search(*args, **kwargs):
        calls.append(credential_id)
        return [replace(row, title=name) for row in original(*args, **kwargs)]

    session.search = search
    return session


@pytest.mark.parametrize("progressive", [False, True])
def test_one_provider_two_configured_credentials_cannot_replay_other_account_cache(
    tmp_path, monkeypatch, progressive,
):
    service = _service(tmp_path)
    try:
        calls = []
        monkeypatch.setattr(service, "_mirror_remote_results", lambda rows, **kwargs: 0)
        def search():
            if progressive:
                return service.search_progressive("Metropolitan").result_count, service.last_search[0].title
            rows = service.search("Metropolitan")
            return len(rows), rows[0].title
        # Same provider ID, selected user identity, scope, query and cache TTL.
        service._sessions = {
            "one_provider": _account_session("credential-A", name="Account A Book", calls=calls)
        }
        assert search() == (1, "Account A Book")
        assert calls == ["credential-A"]
        assert search() == (1, "Account A Book")
        assert calls == ["credential-A"]

        service._sessions = {
            "one_provider": _account_session("credential-B", name="Account B Book", calls=calls)
        }
        assert search() == (1, "Account B Book")
        assert calls == ["credential-A", "credential-B"]
        assert search() == (1, "Account B Book")
        assert calls == ["credential-A", "credential-B"]
    finally:
        service.close()


def test_cache_scope_is_order_stable_and_handles_legacy_provider_sessions():
    a = SimpleNamespace(provider_id="a", credential_id="account-A")
    b = SimpleNamespace(provider_id="b", credential_id="account-B")
    assert _search_session_cache_scope([a, b]) == _search_session_cache_scope([b, a])
    assert _search_session_cache_scope([a]) != _search_session_cache_scope([
        SimpleNamespace(provider_id="a", credential_id="account-C")
    ])
    assert _search_session_cache_scope([SimpleNamespace(provider_id="legacy")]) == (
        ("legacy", ""),
    )
