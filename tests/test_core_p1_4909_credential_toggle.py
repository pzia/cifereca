"""P1 #4909: switching off B's exact credential also revokes A's catalogue.

A live provider session can persist momentarily after Settings disables its
account. Tests use only synthetic sessions, config and local SQLite.
"""
from __future__ import annotations

import pytest

from tests.test_core_p0_4673_paris_foreign_search import _fixture as paris_setup
from tests.test_core_p1_4909_numilog_shared_public import setup as numilog_setup
from tests.test_core_p1_4909_kobo_shared_public import setup as kobo_setup


def _setup(tmp_path, monkeypatch, provider):
    if provider == "paris":
        service, _alice, session, _public, _owners = paris_setup(
            tmp_path, monkeypatch,
        )
    elif provider == "numilog":
        service, session, _owners = numilog_setup(tmp_path, monkeypatch)
    else:
        service, session, _owners = kobo_setup(tmp_path, monkeypatch)
    service._sessions = {"foreign-bob": session}
    _set_enabled(service, session, "config", True)
    _set_enabled(service, session, "sqlite", True)
    return service, session


def _set_enabled(service, session, channel, value):
    provider, credential = session.provider_id, session.credential_id
    if channel == "config":
        service.config.upsert_provider_account({
            "provider": provider, "id": credential,
            "identity_id": "bob", "label": "Synthetic B",
            "enabled": value,
        })
    else:
        service.storage.provider_connection_put({
            "provider_id": provider, "credential_id": credential,
            "identity_id": "bob", "label": "Synthetic B",
            "enabled": value,
        })


def _search(service, provider, progressive, *, force=True, batches=None):
    if progressive:
        summary = service.search_progressive(
            "Progressive Book", provider_id=provider, force=force,
            on_batch=batches.append if batches is not None else None,
        )
        return summary, list(service.last_search)
    return None, service.search(
        "Progressive Book", provider_id=provider, force=force,
    )


def _calls(session, provider):
    return (
        session.calls if provider == "paris"
        else session.public_calls + session.private_calls
    )


@pytest.mark.parametrize("provider", ["paris", "numilog", "kobo"])
@pytest.mark.parametrize("progressive", [False, True])
@pytest.mark.parametrize("switch", ["config_false", "config_bad", "sqlite_false"])
def test_disabling_exact_b_account_rejects_warm_shared_search_cache(
    tmp_path, monkeypatch, provider, progressive, switch,
):
    service, session = _setup(tmp_path, monkeypatch, provider)
    try:
        summary, rows = _search(service, provider, progressive, force=True)
        assert len(rows) == 1 and rows[0].metadata == {
            "catalogue_access": "shared_read_only",
        }
        assert summary is None or not summary.cancelled
        before = _calls(session, provider)
        assert before >= 1

        channel, value = ("sqlite", False) if switch == "sqlite_false" else (
            "config", "false" if switch == "config_bad" else False
        )
        _set_enabled(service, session, channel, value)
        assert service.searchable_providers() == []
        assert provider not in service.provider_search_capabilities()
        for force in (False, True):
            batches = []
            disabled, empty = _search(
                service, provider, progressive, force=force, batches=batches,
            )
            assert empty == [] and batches == []
            assert disabled is None or disabled.providers_total == 0
            assert _calls(session, provider) == before

        _set_enabled(service, session, channel, True)
        summary, again = _search(service, provider, progressive, force=True)
        assert summary is None or not summary.cancelled
        assert len(again) == 1
        assert again[0].metadata == {"catalogue_access": "shared_read_only", **({"language": "fr"} if provider == "paris" else {})}
        assert _calls(session, provider) > before
        if provider != "paris":
            assert session.private_calls == 0
    finally:
        service.close()


@pytest.mark.parametrize("provider", ["paris", "numilog", "kobo"])
@pytest.mark.parametrize("progressive", [False, True])
@pytest.mark.parametrize("channel", ["config", "sqlite"])
def test_disabling_exact_b_account_during_search_cancels_inflight_result(
    tmp_path, monkeypatch, provider, progressive, channel,
):
    service, session = _setup(tmp_path, monkeypatch, provider)
    session.mutate = lambda: _set_enabled(service, session, channel, False)
    try:
        batches = []
        summary, rows = _search(
            service, provider, progressive, force=True, batches=batches,
        )
        assert rows == []
        assert batches == []
        assert summary is None or summary.cancelled
        assert service.last_search == []
        assert provider not in service.provider_search_capabilities()
        assert provider not in dict(service.searchable_providers())
        if provider != "paris":
            assert session.private_calls == 0

        _set_enabled(service, session, channel, True)
        session.mutate = None if provider == "paris" else lambda: None
        recovered, rows = _search(service, provider, progressive, force=False)
        assert recovered is None or not recovered.cancelled
        assert len(rows) == 1
        assert rows[0].metadata == {"catalogue_access": "shared_read_only", **({"language": "fr"} if provider == "paris" else {})}
        if provider != "paris":
            assert session.private_calls == 0
    finally:
        service.close()


def test_unregistered_anonymous_catalogue_keeps_historical_public_route(
    tmp_path, monkeypatch,
):
    service, _a, _bob, public, _owners = paris_setup(tmp_path, monkeypatch)
    service._sessions = {"anonymous-public": public}
    try:
        assert "public-books" in dict(service.searchable_providers())
        assert len(service.search(
            "Progressive Book", provider_id="public-books", force=True,
        )) == 1
    finally:
        service.close()
