"""#4673: search must use the active reader's Paris credential, not the first connected.

Offline, no provider HTTP and no borrow POST. A->B->A plus both sync and
progressive requests prove the resulting source_credential stays scoped.
"""
from dataclasses import replace

import pytest

from tests.test_core043_progress_cache_stats import _FakeSession, _service


def _setup(tmp_path, monkeypatch, *, reverse=False):
    service = _service(tmp_path)
    service.identities.append(replace(
        service.current_identity, identity_id="bob", label="Bob",
    ))
    first = _FakeSession("paris")
    first.credential_id = "paris-alice"
    second = _FakeSession("paris")
    second.credential_id = "paris-bob"
    sessions = [("session-bob", second), ("session-alice", first)]
    if reverse:
        sessions.reverse()
    service._sessions = dict(sessions)
    monkeypatch.setattr(service.storage, "identity_for_connection",
                        lambda provider, credential: {
                            ("paris", "paris-alice"): "alice",
                            ("paris", "paris-bob"): "bob",
                        }.get((provider, credential)))
    monkeypatch.setattr(service, "_mirror_remote_results", lambda rows, **kw: 0)
    return service


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("progressive", [False, True])
def test_active_profile_wins_over_connection_order(tmp_path, monkeypatch, reverse, progressive):
    service = _setup(tmp_path, monkeypatch, reverse=reverse)
    try:
        def run(*, force):
            if progressive:
                summary = service.search_progressive(
                    "Serpentine", provider_id="paris", force=force,
                )
                assert not summary.cancelled
                return service.last_search
            return service.search(
                "Serpentine", provider_id="paris", force=force,
            )

        for profile, credential in (
            ("alice", "paris-alice"),
            ("bob", "paris-bob"),
            ("alice", "paris-alice"),
        ):
            service.select_identity(profile)
            results = run(force=True)
            assert len(results) == 1
            assert results[0].source_provider == "paris"
            assert results[0].source_credential == credential
            # Cache is also scoped by both selected identity and credential.
            cached = run(force=False)
            assert len(cached) == 1
            assert cached[0].source_credential == credential
    finally:
        service.close()


@pytest.mark.parametrize("progressive", [False, True])
def test_disconnected_active_paris_falls_back_to_foreign_catalogue_only(
    tmp_path, monkeypatch, progressive,
):
    service = _setup(tmp_path, monkeypatch)
    foreign_queries = []
    try:
        service._sessions["session-alice"].connected = False
        bob = service._sessions["session-bob"]
        original = bob.search

        def observed(*args, **kwargs):
            foreign_queries.append(True)
            return [
                replace(
                    row, docbase="DILICOM", scenario="EBOOKS",
                    source_collection="catalog", owned=True, downloadable=True,
                    download_url="https://private.invalid/PRIVATE",
                    metadata={"personal": "PRIVATE"},
                )
                for row in original(*args, **kwargs)
            ]

        bob.search = observed
        if progressive:
            summary = service.search_progressive(
                "Serpentine", provider_id="paris", force=True,
            )
            assert not summary.cancelled
            results = service.last_search
        else:
            results = service.search("Serpentine", provider_id="paris", force=True)
        assert len(results) == 1
        assert foreign_queries
        assert results[0].source_credential == "paris-bob"
        assert results[0].metadata == {"catalogue_access": "shared_read_only"}
        assert results[0].owned is False and results[0].downloadable is False
        assert results[0].download_url is None
    finally:
        service.close()


@pytest.mark.parametrize("progressive", [False, True])
def test_session_without_owner_keeps_only_safe_catalogue_projection(
    tmp_path, monkeypatch, progressive,
):
    service = _setup(tmp_path, monkeypatch)
    try:
        monkeypatch.setattr(service.storage, "identity_for_connection",
                            lambda provider, credential: None)
        bob = service._sessions["session-bob"]
        original = bob.search
        bob.search = lambda *a, **kw: [
            replace(
                row, docbase="DILICOM", scenario="EBOOKS",
                source_collection="catalog", owned=True,
                metadata={"private": "PRIVATE"},
            ) for row in original(*a, **kw)
        ]
        if progressive:
            service.search_progressive("Serpentine", provider_id="paris", force=True)
            results = service.last_search
        else:
            results = service.search("Serpentine", provider_id="paris", force=True)
        assert len(results) == 1
        assert results[0].source_credential == "paris-bob"
        assert results[0].owned is False
        assert results[0].metadata == {"catalogue_access": "shared_read_only"}
    finally:
        service.close()
