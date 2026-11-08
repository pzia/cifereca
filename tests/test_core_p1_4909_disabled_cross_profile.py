"""P1 #4909: disabled/disconnected foreign catalogue sessions are never read.

Offline synthetic providers only. A cached shared result must not survive a
disabled source; re-enabling cannot restore a stale rights-bearing projection.
"""
from __future__ import annotations

import pytest

from tests.test_core_p0_4673_paris_foreign_search import _fixture as paris_setup
from tests.test_core_p1_4909_numilog_shared_public import setup as numilog_setup
from tests.test_core_p1_4909_kobo_shared_public import setup as kobo_setup


@pytest.mark.parametrize("progressive", [False, True])
@pytest.mark.parametrize("provider", ["paris", "numilog", "kobo"])
def test_disabled_foreign_session_not_queried_even_after_cached_search(
    tmp_path, monkeypatch, provider, progressive,
):
    if provider == "paris":
        service, _alice, session, _public, _owners = paris_setup(
            tmp_path, monkeypatch,
        )
    elif provider == "numilog":
        service, session, _owners = numilog_setup(tmp_path, monkeypatch)
    else:
        service, session, _owners = kobo_setup(tmp_path, monkeypatch)
    service._sessions = {"foreign-only": session}

    def search(*, force):
        if progressive:
            summary = service.search_progressive(
                "Progressive Book", provider_id=provider, force=force,
            )
            assert not summary.cancelled
            return list(service.last_search)
        return service.search(
            "Progressive Book", provider_id=provider, force=force,
        )

    def calls():
        if provider == "paris":
            return session.calls
        return session.public_calls + session.private_calls

    try:
        first = search(force=True)
        assert len(first) == 1
        assert first[0].metadata == {"catalogue_access": "shared_read_only", **({"language": "fr"} if provider == "paris" else {})}
        before = calls()
        assert before >= 1

        session.connected = False
        assert search(force=False) == []
        assert calls() == before
        assert search(force=True) == []
        assert calls() == before

        session.connected = True
        recovered = search(force=True)
        assert len(recovered) == 1
        assert recovered[0].metadata == {"catalogue_access": "shared_read_only", **({"language": "fr"} if provider == "paris" else {})}
        assert calls() > before
        if provider != "paris":
            assert session.private_calls == 0
    finally:
        service.close()


@pytest.mark.parametrize("progressive", [False, True])
@pytest.mark.parametrize("provider", ["paris", "numilog", "kobo"])
def test_disabled_provider_flag_rejects_cached_foreign_catalogue(
    tmp_path, monkeypatch, provider, progressive,
):
    if provider == "paris":
        service, _, session, _, _ = paris_setup(tmp_path, monkeypatch)
    elif provider == "numilog":
        service, session, _ = numilog_setup(tmp_path, monkeypatch)
    else:
        service, session, _ = kobo_setup(tmp_path, monkeypatch)
    service._sessions = {"foreign": session}
    allowed = {"value": True}
    monkeypatch.setattr(
        service, "_provider_enabled_for_identity",
        lambda provider_id, identity_id=None: allowed["value"],
    )

    def run():
        if progressive:
            return service.search_progressive(
                "Progressive Book", provider_id=provider, force=False,
            )
        return service.search(
            "Progressive Book", provider_id=provider, force=False,
        )

    try:
        # Warm up a catalogue-only result; disabling the plugin must not
        # replay the cached record, even if its session remains connected.
        if progressive:
            first = service.search_progressive(
                "Progressive Book", provider_id=provider, force=True,
            )
            assert first.providers_total == 1
            assert len(service.last_search) == 1
        else:
            assert len(service.search(
                "Progressive Book", provider_id=provider, force=True,
            )) == 1

        allowed["value"] = False
        result = run()
        if progressive:
            assert result.providers_total == 0
        else:
            assert result == []
        assert service.last_search == []
    finally:
        service.close()


@pytest.mark.parametrize("progressive", [False, True])
@pytest.mark.parametrize("provider", ["paris", "numilog", "kobo"])
def test_disabling_provider_during_foreign_search_revokes_results(
    tmp_path, monkeypatch, provider, progressive,
):
    if provider == "paris":
        service, _, session, _, _ = paris_setup(tmp_path, monkeypatch)
    elif provider == "numilog":
        service, session, _ = numilog_setup(tmp_path, monkeypatch)
    else:
        service, session, _ = kobo_setup(tmp_path, monkeypatch)
    service._sessions = {"foreign": session}
    allowed = {"value": True}
    monkeypatch.setattr(
        service, "_provider_enabled_for_identity",
        lambda provider_id, identity_id=None: allowed["value"],
    )
    session.mutate = lambda: allowed.__setitem__("value", False)
    try:
        if progressive:
            result = service.search_progressive(
                "Progressive Book", provider_id=provider, force=True,
            )
            assert result.cancelled
        else:
            assert service.search(
                "Progressive Book", provider_id=provider, force=True,
            ) == []
        assert service.last_search == []

        # A later explicit re-enable can search again, but the cancelled
        # response from the first attempt was never published or cached.
        allowed["value"] = True
        session.mutate = None if provider == "paris" else lambda: None
        if progressive:
            resumed = service.search_progressive(
                "Progressive Book", provider_id=provider, force=False,
            )
            assert not resumed.cancelled
            rows = list(service.last_search)
        else:
            rows = service.search(
                "Progressive Book", provider_id=provider, force=False,
            )
        assert len(rows) == 1
        assert rows[0].metadata == {"catalogue_access": "shared_read_only", **({"language": "fr"} if provider == "paris" else {})}
        if provider != "paris":
            assert session.private_calls == 0
    finally:
        service.close()
