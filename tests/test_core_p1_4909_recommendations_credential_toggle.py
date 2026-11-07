"""P1 #4909: Recommendations must revoke a still-connected B credential.

Real Core search broker + real Recommendations ranking, synthetic sessions.
A disabled source is absent from both the catalogue inventory and ranking,
even when an A-only read-only search was previously cached.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from tests.test_core_p1_4909_recommendations_integration import _recommend
from tests.test_core_p0_4673_paris_foreign_search import _fixture as paris_setup
from tests.test_core_p1_4909_numilog_shared_public import setup as numilog_setup
from tests.test_core_p1_4909_kobo_shared_public import setup as kobo_setup


@pytest.mark.parametrize("provider", ["paris", "numilog", "kobo"])
@pytest.mark.parametrize("switch", ["config", "sqlite", "malformed_config"])
def test_recommendations_discard_b_catalogue_without_waiting_for_session_close(
    tmp_path, monkeypatch, provider, switch,
):
    if provider == "paris":
        service, _, session, _, _ = paris_setup(tmp_path, monkeypatch)
    elif provider == "numilog":
        service, session, _ = numilog_setup(tmp_path, monkeypatch)
    else:
        service, session, _ = kobo_setup(tmp_path, monkeypatch)
    service._sessions = {"bob": session}
    credential = session.credential_id
    identity = "bob"

    def config(enabled):
        service.config.upsert_provider_account({
            "provider": provider, "id": credential,
            "identity_id": identity, "label": "Synthetic owner B",
            "enabled": enabled,
        })

    def sqlite(enabled):
        service.storage.provider_connection_put({
            "provider_id": provider, "credential_id": credential,
            "identity_id": identity, "label": "Synthetic owner B",
            "enabled": enabled,
        })

    config(True)
    sqlite(True)

    original_search = session.search

    def matched_search(query, scope, **kwargs):
        return [
            replace(row, author="Alice")
            for row in original_search(query, scope, **kwargs)
        ]

    session.search = matched_search

    def calls():
        return session.calls if provider == "paris" else (
            session.public_calls + session.private_calls
        )

    try:
        first, _ = _recommend(service, "alice")
        assert len(first.ranked) == 1
        assert first.providers == ((provider, session.descriptor.display_name),)
        assert first.ranked[0].item.metadata == {
            "catalogue_access": "shared_read_only",
        }
        before = calls()
        assert before >= 1

        if switch == "config":
            config(False)
        elif switch == "malformed_config":
            config("false")
        else:
            sqlite(False)

        # The session remains connected; the source switch must be honored
        # without requiring connect_configured() or publishing its cache.
        assert session.connected is True
        disabled, partials = _recommend(service, "alice")
        assert disabled.providers == ()
        assert disabled.ranked == ()
        assert partials == []
        assert calls() == before

        if switch == "sqlite":
            sqlite(True)
        else:
            config(True)

        restored, _ = _recommend(service, "alice")
        assert len(restored.ranked) == 1
        assert restored.ranked[0].item.metadata == {
            "catalogue_access": "shared_read_only",
        }
        assert restored.ranked[0].item.owned is False
        assert restored.ranked[0].item.downloadable is False
        if provider != "paris":
            assert session.private_calls == 0
    finally:
        service.close()
