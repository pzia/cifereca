"""P1 #4909: a foreign Numilog login is bibliography-only, never MesLivres.

Synthetic provider with distinct public/catalogue and owned/library routes.
No real Numilog HTTP or credentials. Both Core search paths are exercised.
"""
from dataclasses import asdict, replace

import pytest

from tests.test_core043_progress_cache_stats import _FakeSession, _service


class NumilogFixture(_FakeSession):
    def __init__(self):
        super().__init__("numilog")
        self.credential_id = "numilog-bob"
        self.private_calls = 0
        self.public_calls = 0
        self.mutate = lambda: None
        self.holdings_calls = 0

    def search(self, query, scope, **kwargs):
        base = super().search(query, scope, **kwargs)
        self.mutate()
        if scope == "catalog":
            self.public_calls += 1
            public = replace(
                base[0], source_credential="__public__",
                docbase="NUMILOG", scenario="NUMILOG_PUBLIC",
                source_collection="public_catalog", owned=True,
                downloadable=True, download_url="https://private.invalid/SECRET",
                metadata={"private_account": "SECRET"},
                friendly_url="https://private.invalid/SECRET",
                thumbnail_url="https://private.invalid/SECRET",
            )
            illicit = replace(
                base[0], source_credential="numilog-bob",
                scenario="NUMILOG_LIBRARY", source_collection="library",
                owned=True, downloadable=True,
            )
            return [public, illicit]
        self.private_calls += 1
        return [replace(
            base[0], source_credential="numilog-bob", docbase="NUMILOG",
            scenario="NUMILOG_LIBRARY", source_collection="library",
            owned=True, downloadable=True, metadata={"private_account": "BOB"},
        )]

    def holdings(self, *args, **kwargs):
        self.holdings_calls += 1
        raise AssertionError("foreign private Numilog holdings forbidden")


def setup(tmp_path, monkeypatch):
    service = _service(tmp_path)
    service.identities.append(replace(
        service.current_identity, identity_id="bob", label="Bob",
    ))
    session = NumilogFixture()
    service._sessions = {"bob": session}
    owners = {("numilog", "numilog-bob"): "bob"}
    monkeypatch.setattr(
        service.storage, "identity_for_connection",
        lambda provider, credential: owners.get((provider, credential)),
    )
    monkeypatch.setattr(
        service, "_mirror_remote_results", lambda *args, **kwargs: 0,
    )
    return service, session, owners


def run(service, progressive, *, force=True, on_batch=None, scope="all"):
    if progressive:
        summary = service.search_progressive(
            "Progressive Book", provider_id="numilog", scope=scope,
            force=force, on_batch=on_batch,
        )
        assert not summary.cancelled
        return list(service.last_search)
    return service.search(
        "Progressive Book", provider_id="numilog", scope=scope, force=force,
    )


@pytest.mark.parametrize("progressive", [False, True])
@pytest.mark.parametrize("scope", ["all", "digital"])
def test_foreign_numilog_search_uses_only_anonymous_catalogue(
    tmp_path, monkeypatch, progressive, scope,
):
    service, session, owners = setup(tmp_path, monkeypatch)
    batches = []
    try:
        shared = run(
            service, progressive, scope=scope, on_batch=batches.append,
        )
        assert session.private_calls == 0 and session.public_calls >= 1
        assert len(shared) == 1
        public = shared[0]
        assert (public.source_provider, public.source_credential,
                public.scenario, public.source_collection) == (
                    "numilog", "__public__", "NUMILOG_PUBLIC", "public_catalog",
                )
        assert public.owned is False and public.downloadable is False
        assert public.download_url is None
        assert public.thumbnail_url is None and public.friendly_url is None
        assert public.metadata == {"catalogue_access": "shared_read_only"}
        assert "SECRET" not in repr(asdict(public))
        if progressive:
            assert len(batches) == 1
            assert batches[0].results == (public,)
            assert batches[0].response_facets == ()
        detail = service.holdings(public, force=True)
        assert detail.availability_status == "unknown"
        assert detail.holdings == []
        assert session.holdings_calls == 0
        assert service.can_direct_download(public) is False
        before = session.public_calls
        assert run(service, progressive, force=False, scope=scope) == shared
        assert session.public_calls == before
        service.select_identity("bob")
        own = run(service, progressive, force=False, scope=scope)
        assert len(own) == 1 and own[0].owned is True
        assert own[0].metadata == {"private_account": "BOB"}
        assert session.private_calls >= 1
        service.select_identity("alice")
        assert run(service, progressive, force=False, scope=scope) == shared
    finally:
        service.close()


@pytest.mark.parametrize("progressive", [False, True])
def test_numilog_owner_rebound_during_public_read_revokes_response(
    tmp_path, monkeypatch, progressive,
):
    service, session, owners = setup(tmp_path, monkeypatch)
    session.mutate = lambda: owners.__setitem__(("numilog", "numilog-bob"), "alice")
    try:
        if progressive:
            summary = service.search_progressive(
                "Progressive Book", provider_id="numilog", force=True,
            )
            assert summary.cancelled
        else:
            assert service.search(
                "Progressive Book", provider_id="numilog", force=True,
            ) == []
        assert service.last_search == []
        assert session.private_calls == 0
    finally:
        service.close()


@pytest.mark.parametrize("progressive", [False, True])
def test_numilog_unknown_owner_cannot_fall_back_to_meslivres(
    tmp_path, monkeypatch, progressive,
):
    service, session, owners = setup(tmp_path, monkeypatch)
    owners.clear()
    try:
        public = run(service, progressive)
        assert len(public) == 1
        assert public[0].source_credential == "__public__"
        assert session.private_calls == 0
    finally:
        service.close()
