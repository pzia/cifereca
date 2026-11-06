"""P0 #4673: an explicitly foreign Paris account is never a search fallback.

Exercise both production search paths against synthetic sessions and SQLite
identity selection. No provider HTTP, loan attempt, GET acquisition or POST.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from tests.test_core043_progress_cache_stats import _FakeSession, _service


def _fixture(tmp_path, monkeypatch):
    service = _service(tmp_path)
    service.identities.append(replace(
        service.current_identity, identity_id="bob", label="Bob",
    ))
    alice = _FakeSession("paris")
    alice.credential_id = "paris-alice"
    bob = _FakeSession("paris")
    bob.credential_id = "paris-bob"
    public = _FakeSession("public-books")
    owners = {
        ("paris", "paris-alice"): "alice",
        ("paris", "paris-bob"): "bob",
    }
    monkeypatch.setattr(
        service.storage, "identity_for_connection",
        lambda provider, credential: owners.get((provider, credential)),
    )
    monkeypatch.setattr(
        service, "_mirror_remote_results", lambda *args, **kwargs: 0,
    )
    return service, alice, bob, public


def _run(service, progressive, *, provider="paris"):
    if progressive:
        summary = service.search_progressive(
            "Progressive Book", provider_id=provider, force=True,
        )
        assert not summary.cancelled
        return list(service.last_search)
    return service.search("Progressive Book", provider_id=provider, force=True)


@pytest.mark.parametrize("progressive", [False, True])
def test_only_foreign_paris_session_fails_closed_instead_of_searching_it(
    tmp_path, monkeypatch, progressive,
):
    service, alice, bob, _ = _fixture(tmp_path, monkeypatch)
    invoked = []
    original = bob.search
    bob.search = lambda *a, **kw: invoked.append("foreign-search") or original(*a, **kw)
    service._sessions = {"bob-connected-first": bob}
    try:
        # Alice cannot borrow from Bob, but must not query or present his
        # credential as an apparently local Paris catalogue either.
        assert _run(service, progressive) == []
        assert service.last_search == []
        assert invoked == []

        # Reversing the selected profile makes Bob's own source legitimate.
        service.select_identity("bob")
        own = _run(service, progressive)
        assert len(own) == 1
        assert own[0].source_credential == "paris-bob"
        assert invoked

        # B -> A must not replay B's cache or expose B's previous result.
        service.select_identity("alice")
        before = len(invoked)
        assert _run(service, progressive) == []
        assert service.last_search == []
        assert len(invoked) == before
    finally:
        service.close()


@pytest.mark.parametrize("progressive", [False, True])
def test_foreign_paris_is_skipped_without_hiding_unrelated_public_catalogue(
    tmp_path, monkeypatch, progressive,
):
    service, _, bob, public = _fixture(tmp_path, monkeypatch)
    calls = []
    bob.search = lambda *_a, **_kw: calls.append("foreign") or []
    service._sessions = {"foreign-paris": bob, "public": public}
    try:
        rows = _run(service, progressive, provider="all")
        assert len(rows) == 1
        assert rows[0].source_provider == "public-books"
        assert calls == []
    finally:
        service.close()


@pytest.mark.parametrize("progressive", [False, True])
def test_valid_owned_paris_source_still_wins_with_foreign_connected_first(
    tmp_path, monkeypatch, progressive,
):
    service, alice, bob, _ = _fixture(tmp_path, monkeypatch)
    service._sessions = {"bob-connected-first": bob, "alice": alice}
    try:
        rows = _run(service, progressive)
        assert len(rows) == 1
        assert rows[0].source_credential == "paris-alice"
        service.select_identity("bob")
        rows = _run(service, progressive)
        assert len(rows) == 1
        assert rows[0].source_credential == "paris-bob"
        service.select_identity("alice")
        rows = _run(service, progressive)
        assert len(rows) == 1
        assert rows[0].source_credential == "paris-alice"
    finally:
        service.close()
