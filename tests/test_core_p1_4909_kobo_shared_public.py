"""#4909: a cross-profile Kobo search must be public even with B connected.

Synthetic session differentiates public /catalog and private /library search.
No Vault network, tokens, DRM operations or real entitlement mutations.
"""
from dataclasses import asdict, replace

import pytest

from tests.test_core043_progress_cache_stats import _FakeSession, _service


class KoboFixture(_FakeSession):
    def __init__(self):
        super().__init__("kobo")
        self.credential_id = "kobo-bob"
        self.public_calls = 0
        self.private_calls = 0
        self.holdings_calls = 0
        self.mutate = lambda: None

    def search(self, query, scope, **kwargs):
        base = super().search(query, scope, **kwargs)[0]
        self.mutate()
        if scope == "catalog":
            self.public_calls += 1
            public = replace(
                base, source_credential=self.credential_id,
                scenario="KOBO", docbase="KOBO",
                source_collection="catalog", owned=False,
                downloadable=False, download_url=None,
                metadata={"private": "SECRET", "account_id": "BOB"},
                thumbnail_url="https://private.invalid/SECRET",
                friendly_url="https://private.invalid/SECRET",
            )
            private = replace(
                public, source_collection="library", owned=True,
                downloadable=True, metadata={"private": "BOB"},
            )
            signed = replace(public, id="kobo:malicious", download_url="SECRET")
            return [public, private, signed]
        self.private_calls += 1
        return [replace(
            base, source_credential=self.credential_id,
            scenario="KOBO", docbase="KOBO",
            source_collection="library", owned=True, downloadable=True,
            metadata={"private": "BOB"},
        )]

    def holdings(self, *_args, **_kwargs):
        self.holdings_calls += 1
        raise AssertionError("No foreign Kobo holdings call")


def setup(tmp_path, monkeypatch):
    service = _service(tmp_path)
    service.identities.append(replace(
        service.current_identity, identity_id="bob", label="Bob",
    ))
    session = KoboFixture()
    service._sessions = {"bob": session}
    owners = {("kobo", "kobo-bob"): "bob"}
    monkeypatch.setattr(
        service.storage, "identity_for_connection",
        lambda provider, credential: owners.get((provider, credential)),
    )
    monkeypatch.setattr(
        service, "_mirror_remote_results", lambda *args, **kwargs: 0,
    )
    return service, session, owners


def run(service, progressive, *, force=True, scope="all", on_batch=None):
    if progressive:
        summary = service.search_progressive(
            "Progressive Book", provider_id="kobo", scope=scope,
            force=force, on_batch=on_batch,
        )
        assert not summary.cancelled
        return list(service.last_search)
    return service.search(
        "Progressive Book", provider_id="kobo", scope=scope, force=force,
    )


@pytest.mark.parametrize("progressive", [False, True])
@pytest.mark.parametrize("scope", ["all", "digital"])
def test_foreign_kobo_query_uses_catalogue_only(
    tmp_path, monkeypatch, progressive, scope,
):
    service, session, _owners = setup(tmp_path, monkeypatch)
    batches = []
    try:
        rows = run(service, progressive, scope=scope, on_batch=batches.append)
        assert session.private_calls == 0 and session.public_calls >= 1
        assert len(rows) == 1
        public = rows[0]
        assert (public.source_provider, public.source_credential,
                public.scenario, public.source_collection) == (
                    "kobo", "kobo-bob", "KOBO", "catalog",
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
        assert run(service, progressive, scope=scope, force=False) == rows
        assert session.public_calls == before

        service.select_identity("bob")
        own = run(service, progressive, scope=scope, force=False)
        assert len(own) == 1 and own[0].owned is True
        assert own[0].metadata == {"private": "BOB"}
        assert session.private_calls >= 1
        service.select_identity("alice")
        assert run(service, progressive, scope=scope, force=False) == rows
    finally:
        service.close()


@pytest.mark.parametrize("progressive", [False, True])
def test_kobo_owner_rebound_during_public_search_revokes_response(
    tmp_path, monkeypatch, progressive,
):
    service, session, owners = setup(tmp_path, monkeypatch)
    session.mutate = lambda: owners.__setitem__(("kobo", "kobo-bob"), "alice")
    try:
        if progressive:
            summary = service.search_progressive(
                "Progressive Book", provider_id="kobo", force=True,
            )
            assert summary.cancelled
        else:
            assert service.search(
                "Progressive Book", provider_id="kobo", force=True,
            ) == []
        assert service.last_search == []
        assert session.private_calls == 0
    finally:
        service.close()


@pytest.mark.parametrize("progressive", [False, True])
def test_unknown_kobo_owner_is_public_only(tmp_path, monkeypatch, progressive):
    service, session, owners = setup(tmp_path, monkeypatch)
    owners.clear()
    try:
        rows = run(service, progressive)
        assert len(rows) == 1
        assert rows[0].metadata == {"catalogue_access": "shared_read_only"}
        assert session.private_calls == 0
    finally:
        service.close()
