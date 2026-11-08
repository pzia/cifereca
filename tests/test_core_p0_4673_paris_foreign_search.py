"""#4909: foreign Paris bibliography is shared; foreign rights never are.

This intentionally supersedes the P0 #4673 ban on searching B's catalogue
from A. Both search paths keep the source owner; all effects are offline.
"""
from __future__ import annotations

from dataclasses import asdict, replace

import pytest

from tests.test_core043_progress_cache_stats import _FakeSession, _service


class _ParisCatalogue(_FakeSession):
    def __init__(self, credential):
        super().__init__("paris")
        self.credential_id = credential
        self.calls = 0
        self.mutate = None

    def search(self, query, scope, **kwargs):
        self.calls += 1
        if self.mutate is not None:
            self.mutate()
        return [replace(
            row, docbase="DILICOM", scenario="EBOOKS",
            source_collection="catalog",
            owned=True, downloadable=True,
            download_url="https://private.invalid/?token=PRIVATE",
            thumbnail_url="https://private.invalid/cover?token=PRIVATE",
            friendly_url="https://private.invalid/profile/PRIVATE",
            revision_id="PRIVATE", metadata={"account": "PRIVATE", "language": "fr"},
        ) for row in super().search(query, scope, **kwargs)]


def _fixture(tmp_path, monkeypatch):
    service = _service(tmp_path)
    service.identities.append(replace(
        service.current_identity, identity_id="bob", label="Bob",
    ))
    alice = _ParisCatalogue("paris-alice")
    bob = _ParisCatalogue("paris-bob")
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
    return service, alice, bob, public, owners


def _run(service, progressive, *, provider="paris", force=True, batches=None):
    if progressive:
        summary = service.search_progressive(
            "Progressive Book", provider_id=provider, force=force,
            on_batch=batches.append if batches is not None else None,
        )
        assert not summary.cancelled
        return list(service.last_search)
    return service.search("Progressive Book", provider_id=provider, force=force)


def _assert_shared(row):
    assert row.source_provider == "paris"
    assert row.source_credential == "paris-bob"
    assert row.scenario == "EBOOKS" and row.docbase == "DILICOM"
    assert row.metadata == {"catalogue_access": "shared_read_only", "language": "fr"}
    assert row.owned is False and row.downloadable is False
    assert row.download_url is None and row.thumbnail_url is None
    assert row.friendly_url is None and row.revision_id is None
    assert "PRIVATE" not in repr(asdict(row))


@pytest.mark.parametrize("progressive", [False, True])
def test_b_only_is_visible_to_a_as_read_only_catalogue_and_cache_is_partitioned(
    tmp_path, monkeypatch, progressive,
):
    service, _, bob, _, _ = _fixture(tmp_path, monkeypatch)
    service._sessions = {"bob-first": bob}
    batches = []
    try:
        shared = _run(service, progressive, batches=batches)
        assert len(shared) == 1 and bob.calls >= 1
        _assert_shared(shared[0])
        if progressive:
            assert len(batches) == 1
            _assert_shared(batches[0].results[0])
            assert batches[0].response_facets == ()
        before = bob.calls
        _assert_shared(_run(service, progressive, force=False)[0])
        assert bob.calls == before

        # B's own profile must not receive A's sanitised catalogue cache.
        service.select_identity("bob")
        own = _run(service, progressive, force=False)
        assert len(own) == 1 and own[0].source_credential == "paris-bob"
        assert own[0].owned is True and own[0].metadata["account"] == "PRIVATE"

        # A's cache remains catalogue-only after the round trip B -> A.
        service.select_identity("alice")
        _assert_shared(_run(service, progressive, force=False)[0])
    finally:
        service.close()


@pytest.mark.parametrize("progressive", [False, True])
def test_foreign_catalogue_is_visible_beside_unrelated_public_source(
    tmp_path, monkeypatch, progressive,
):
    service, _, bob, public, _ = _fixture(tmp_path, monkeypatch)
    service._sessions = {"foreign-paris": bob, "public": public}
    try:
        rows = _run(service, progressive, provider="all")
        assert len(rows) == 2
        _assert_shared(next(row for row in rows if row.source_provider == "paris"))
        assert any(row.source_provider == "public-books" for row in rows)
    finally:
        service.close()


@pytest.mark.parametrize("progressive", [False, True])
def test_own_paris_wins_foreign_regardless_of_connection_order(
    tmp_path, monkeypatch, progressive,
):
    service, alice, bob, _, _ = _fixture(tmp_path, monkeypatch)
    service._sessions = {"bob-connected-first": bob, "alice": alice}
    try:
        for identity, credential in (
            ("alice", "paris-alice"), ("bob", "paris-bob"), ("alice", "paris-alice"),
        ):
            service.select_identity(identity)
            rows = _run(service, progressive)
            assert len(rows) == 1
            assert rows[0].source_credential == credential
            assert rows[0].metadata.get("catalogue_access") is None
    finally:
        service.close()


@pytest.mark.parametrize("progressive", [False, True])
def test_owner_rebound_during_supplier_read_revokes_old_foreign_result(
    tmp_path, monkeypatch, progressive,
):
    service, _, bob, _, owners = _fixture(tmp_path, monkeypatch)
    service._sessions = {"only-bob": bob}
    bob.mutate = lambda: owners.__setitem__(("paris", "paris-bob"), "alice")
    try:
        if progressive:
            summary = service.search_progressive(
                "Progressive Book", provider_id="paris", force=True,
            )
            assert summary.cancelled
        else:
            assert service.search(
                "Progressive Book", provider_id="paris", force=True,
            ) == []
        assert service.last_search == []
    finally:
        service.close()


@pytest.mark.parametrize("progressive", [False, True])
def test_foreign_or_unverified_paris_details_never_query_b_account(
    tmp_path, monkeypatch, progressive,
):
    service, _, bob, _, owners = _fixture(tmp_path, monkeypatch)
    service._sessions = {"only-bob": bob}
    bob.holdings = lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("foreign holdings GET forbidden")
    )
    try:
        row = _run(service, progressive)[0]
        detail = service.holdings(row, force=True)
        assert detail.availability_status == "unknown"
        assert detail.holdings == []
        assert detail.source_credential == "paris-bob"
        assert service.can_direct_download(row) is False
        # The immutable read-only projection stays inert on profile change.
        service.select_identity("bob")
        assert service.holdings(row, force=True).holdings == []
        owners[("paris", "paris-bob")] = None
        service.select_identity("alice")
        assert service.holdings(row, force=True).holdings == []
    finally:
        service.close()


@pytest.mark.parametrize("progressive", [False, True])
def test_foreign_inventory_rows_cannot_enter_catalogue(
    tmp_path, monkeypatch, progressive,
):
    service, _, bob, _, _ = _fixture(tmp_path, monkeypatch)
    service._sessions = {"only-bob": bob}
    original = bob.search
    bob.search = lambda *a, **kw: [
        replace(row, scenario="PERSONAL_LIBRARY", source_collection="library")
        for row in original(*a, **kw)
    ]
    try:
        assert _run(service, progressive) == []
    finally:
        service.close()


@pytest.mark.parametrize("progressive", [False, True])
def test_foreign_catalogue_keeps_safe_public_cover_without_entitlements(
    tmp_path, monkeypatch, progressive,
):
    """Public catalogue thumbnails are reusable in A, unlike B's account URLs."""
    service, _, bob, _, _ = _fixture(tmp_path, monkeypatch)
    original = bob.search
    public_cover = "https://bibliotheques.paris.fr/media/cover.jpg"
    bob.search = lambda *args, **kwargs: [
        replace(row, thumbnail_url=public_cover)
        for row in original(*args, **kwargs)
    ]
    service._sessions = {"foreign-paris": bob}
    try:
        rows = _run(service, progressive)
        assert len(rows) == 1
        shared = rows[0]
        assert shared.thumbnail_url == public_cover
        assert shared.metadata == {"catalogue_access": "shared_read_only"}
        assert shared.owned is False and shared.downloadable is False
        assert shared.download_url is None and shared.friendly_url is None
        assert "PRIVATE" not in repr(asdict(shared))
        assert _run(service, progressive, force=False)[0].thumbnail_url == public_cover
        service.select_identity("bob")
        own = _run(service, progressive, force=False)[0]
        assert own.thumbnail_url == public_cover
        assert own.owned is True
        service.select_identity("alice")
        assert _run(service, progressive, force=False)[0].thumbnail_url == public_cover
    finally:
        service.close()


@pytest.mark.parametrize("url", [
    "http://bibliotheques.paris.fr/media/cover.jpg",
    "https://private.invalid/cover.jpg",
    "https://bibliotheques.paris.fr/media/cover.jpg?token=PRIVATE",
    "https://bibliotheques.paris.fr/profile/PRIVATE/cover.jpg",
    "https://bibliotheques.paris.fr/session/abc/image",
    "https://bibliotheques.paris.fr/download/cover.jpg",
    "https://bibliotheques.paris.fr/media/cover.jpg#PRIVATE",
    "https://user:pass@bibliotheques.paris.fr/media/cover.jpg",
    "https://127.0.0.1/image.jpg",
    "file:///tmp/cover.jpg",
    "//bibliotheques.paris.fr/media/cover.jpg",
    None,
])
@pytest.mark.parametrize("progressive", [False, True])
def test_foreign_paris_catalogue_rejects_nonpublic_and_signed_cover(
    tmp_path, monkeypatch, progressive, url,
):
    service, _, bob, _, _ = _fixture(tmp_path, monkeypatch)
    original = bob.search
    bob.search = lambda *args, **kwargs: [
        replace(row, thumbnail_url=url)
        for row in original(*args, **kwargs)
    ]
    service._sessions = {"foreign-paris": bob}
    try:
        shared = _run(service, progressive)[0]
        assert shared.thumbnail_url is None
        assert shared.owned is False and shared.download_url is None
        assert "PRIVATE" not in repr(asdict(shared))
    finally:
        service.close()



@pytest.mark.parametrize("progressive", [False, True])
def test_foreign_paris_catalogue_keeps_safe_bibliography_without_foreign_extras(
    tmp_path, monkeypatch, progressive,
):
    """The Core identity selector is not an ACL for catalogue language/facets."""
    from cifereca.core.search_orchestration import _shared_paris_catalogue_row
    service, _, bob, _, _ = _fixture(tmp_path, monkeypatch)
    service._sessions = {"bob": bob}
    try:
        row = _run(service, progressive)[0]
        assert row.metadata["language"] == "fr"
        assert row.metadata["catalogue_access"] == "shared_read_only"
        assert "account" not in row.metadata
        assert row.thumbnail_url is None  # Private token URL is still not exposed.

        original = replace(
            row,
            metadata={
                "language": "fr", "languages": ["fr", "en"],
                "series": "Romans", "collection": "Littérature",
                "publisher": "Éditeur", "isbn": "9780306406157",
                "account": "FOREIGN_SECRET", "rights": {"access_token": "SECRET"},
                "session": "FOREIGN_SECRET", "language_original": "en",
                "subject": "https://private.invalid?token=SECRET",
                "bibliography_evidence": {
                    "subjects": {
                        "source": "paris.syracuse.search",
                        "source_field": "Resource.Subj",
                        "scope": "EBOOKS",
                        "confidence": "upstream_field_present",
                        "freshness": "search_response",
                    }
                },
                "subjects": ["Science-fiction", "Histoire"],
            },
        )
        qualified = _shared_paris_catalogue_row(original, bob)
        assert qualified is not None
        assert qualified.metadata == {
            "catalogue_access": "shared_read_only",
            "language": "fr", "languages": ["fr", "en"],
            "series": "Romans", "collection": "Littérature",
            "publisher": "Éditeur", "isbn": "9780306406157",
            "subjects": ["Science-fiction", "Histoire"],
            "bibliography_evidence": {
                "subjects": {
                    "source": "paris.syracuse.search",
                    "source_field": "Resource.Subj",
                    "scope": "EBOOKS",
                    "confidence": "upstream_field_present",
                    "freshness": "search_response",
                }
            },
        }
        assert "FOREIGN_SECRET" not in repr(asdict(qualified))
        assert "access_token" not in repr(asdict(qualified))
    finally:
        service.close()


@pytest.mark.parametrize("unsafe", [
    "https://private.invalid/account",
    "Bearer Secret", "token=SECRET", "reader@example.com",
    "bad\\x00value",  # NUL is not bibliographic data
])
def test_shared_paris_bibliography_rejects_non_bibliographic_values(unsafe):
    from cifereca.core.search_orchestration import _shared_paris_catalogue_bibliography
    from tests.test_core043_progress_cache_stats import _FakeSession
    session = _FakeSession("paris")
    source = replace(
        session.search("term", "all")[0],
        source_provider="paris", source_credential="bob",
        scenario="EBOOKS", docbase="DILICOM",
        metadata={"language": unsafe, "series": unsafe, "account": "SECRET"},
    )
    metadata = _shared_paris_catalogue_bibliography(source)
    assert metadata == {"catalogue_access": "shared_read_only"}
