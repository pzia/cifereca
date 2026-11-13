"""P0 #5802 — exact public provenance in all federated search paths."""
from dataclasses import asdict, replace
from threading import Event, Thread

import pytest

from cifereca.plugin_api import ProviderSearchResponse, PublicSearchNotice
from tests.test_core043_progress_cache_stats import _FakeSession, _service


class ExplicitPublic(_FakeSession):
    def __init__(self, provider_id, credential=None):
        super().__init__(provider_id)
        self.credential_id = credential or provider_id + "-credential"
        self.typed_calls = 0
        self.legacy_calls = 0
        self.notices = True
        self.duplicate = False
        self.rebind = None
        self.partial = False
        self.wrong_source = False

    def search(self, query, scope, **kwargs):
        self.legacy_calls += 1
        return [replace(
            row, owned=True, downloadable=True,
            download_url="https://private.invalid/download?secret=VALUE",
            metadata={"account": "PRIVATE"}, source_collection="catalog",
        ) for row in super().search(query, scope, **kwargs)]

    def search_with_response_facets(self, query, scope, *, field, content_types):
        self.typed_calls += 1
        rows = self.search(query, scope, field=field, content_types=content_types)
        if self.rebind:
            self.rebind()
        row = rows[0]
        public = replace(
            row, owned=False, downloadable=False, download_url=None,
            source_account_id=None, metadata={"language": "fr"},
            title="Public book", friendly_url=None, thumbnail_url=None,
        )
        if self.wrong_source:
            public = replace(public, source_provider="other")
        notices = (PublicSearchNotice(public),) if self.notices else ()
        if self.duplicate:
            notices += notices
        return ProviderSearchResponse(
            rows, partial=self.partial, public_notices=notices,
        )


def _setup(tmp_path, monkeypatch, providers):
    service = _service(tmp_path)
    owner = {(session.provider_id, session.credential_id): "bob"
             for session in providers}
    monkeypatch.setattr(
        service.storage, "identity_for_connection",
        lambda provider, credential: owner.get((provider, credential)),
    )
    monkeypatch.setattr(
        service, "_mirror_remote_results", lambda *args, **kwargs: 0,
    )
    service._sessions = {str(i): session for i, session in enumerate(providers)}
    return service, owner


def _run(service, progressive, **kwargs):
    if progressive:
        batches = []
        summary = service.search_progressive(
            "Public book", force=True, on_batch=batches.append, **kwargs,
        )
        return list(service.last_search), batches, summary
    return service.search("Public book", force=True, **kwargs), [], None


@pytest.mark.parametrize("progressive", [False, True])
@pytest.mark.parametrize("provider", ["synthetic-one", "synthetic-two", "unknown-provider"])
def test_foreign_exact_public_notice_only_no_private_fields(
    tmp_path, monkeypatch, progressive, provider,
):
    session = ExplicitPublic(provider)
    service, _ = _setup(tmp_path, monkeypatch, [session])
    try:
        rows, batches, _ = _run(service, progressive)
        assert len(rows) == 1
        row = rows[0]
        assert row.source_provider == provider
        assert row.source_credential == session.credential_id
        assert row.title == "Public book"
        assert row.metadata == {
            "catalogue_access": "shared_read_only", "language": "fr",
        }
        assert not row.owned and not row.downloadable and row.download_url is None
        assert "PRIVATE" not in repr(asdict(row))
        assert "VALUE" not in repr(asdict(row))
        assert session.typed_calls >= 1
        if progressive:
            assert len(batches) == 1
            assert batches[0].results == tuple(rows)
            assert batches[0].response_facets == ()
        # The owner retains its private, unprojected source search.
        service.current_identity = replace(
            service.current_identity, identity_id="bob",
        )
        own = service.search("Public book", force=True)
        assert own[0].owned is True
        assert own[0].metadata["account"] == "PRIVATE"
    finally:
        service.close()


@pytest.mark.parametrize("progressive", [False, True])
@pytest.mark.parametrize("kind", ["missing", "duplicate", "wrong-source"])
def test_malformed_ambiguous_or_missing_public_notice_fails_closed(
    tmp_path, monkeypatch, progressive, kind,
):
    session = ExplicitPublic("synthetic-one")
    session.notices = kind != "missing"
    session.duplicate = kind == "duplicate"
    session.wrong_source = kind == "wrong-source"
    service, _ = _setup(tmp_path, monkeypatch, [session])
    try:
        rows, batches, _ = _run(service, progressive)
        assert rows == []
        if progressive:
            assert batches and not batches[0].results
    finally:
        service.close()


@pytest.mark.parametrize("progressive", [False, True])
def test_legacy_list_only_foreign_has_no_public_authority(
    tmp_path, monkeypatch, progressive,
):
    service, _ = _setup(tmp_path, monkeypatch, [_FakeSession("legacy")])
    try:
        rows, _, _ = _run(service, progressive)
        assert rows == []
    finally:
        service.close()


@pytest.mark.parametrize("progressive", [False, True])
def test_owner_change_mid_response_revokes_public_projection(
    tmp_path, monkeypatch, progressive,
):
    session = ExplicitPublic("synthetic-one")
    service, owners = _setup(tmp_path, monkeypatch, [session])
    session.rebind = lambda: owners.__setitem__(
        (session.provider_id, session.credential_id), "alice",
    )
    try:
        rows, batches, summary = _run(service, progressive)
        assert rows == []
        assert not batches
        if progressive:
            assert summary.cancelled
    finally:
        service.close()
