from dataclasses import replace
from types import SimpleNamespace

import pytest

from cifereca.models import SearchResult
from cifereca.plugin_api import ProviderSearchResponse, PublicSearchNotice
from cifereca.core.public_search_projection import project_public_search_result
from cifereca.core.search_orchestration import _public_rows_from_response, _configured_search_preferences


def row(provider='synthetic-a', credential='source-b', **changes):
    values = dict(
        id='item', record_id='edition', docbase='catalogue', medium='digital',
        title='Public title', author='Author', type='Book', format='EPUB',
        publisher=None, published_at=None, description=None,
        thumbnail_url=None, friendly_url=None, scenario='search',
        source_provider=provider, source_credential=credential,
        source_collection='catalogue',
    )
    values.update(changes)
    return SearchResult(**values)


def project(response, item, **scope):
    return project_public_search_result(
        response, item, provider_id=scope.get('provider', item.source_provider),
        credential_id=scope.get('credential', item.source_credential),
    )


@pytest.mark.parametrize('provider', ['synthetic-a', 'synthetic-b', 'unknown'])
def test_public_parser_projection_is_not_private_source_authority(provider):
    private = row(provider, title='Private annotation', owned=True,
                  source_account_id='borrower-b', download_url='https://secret/token')
    public = row(provider, metadata={'subjects': ['Novel'],
                                    'account': 'borrower-b',
                                    'manual_borrow_presentation': True})
    response = ProviderSearchResponse([private], (), False, (PublicSearchNotice(public),))
    actual = project(response, private)
    assert actual.title == 'Public title'
    assert actual.source_credential == 'source-b'
    assert actual.source_account_id is None
    assert actual.owned is False and actual.downloadable is False
    assert actual.download_url is None and actual.document_classification is None
    assert actual.metadata == {'catalogue_access': 'shared_read_only', 'subjects': ['Novel']}
    actual.metadata['subjects'].append('Changed')
    assert public.metadata['subjects'] == ['Novel']
    assert private.owned is True
    # A reader in profile A cannot rebind the public provenance from B to A.
    assert project(response, private, credential='source-a') is None


def test_absent_capability_does_not_promote_legacy_rows():
    item = row(metadata={'catalogue_access': 'public'}, owned=False)
    assert project(ProviderSearchResponse([item]), item) is None
    assert ProviderSearchResponse([item], (), True).partial is True


@pytest.mark.parametrize('field,value', [
    ('id', 'other'), ('record_id', 'other-edition'), ('docbase', 'other-branch'),
    ('scenario', 'other-scenario'), ('source_provider', 'synthetic-b'),
    ('source_credential', 'source-a'), ('source_collection', 'other-collection'),
    ('medium', 'physical'),
])
def test_every_source_coordinate_is_exact(field, value):
    item = row()
    public = replace(item, **{field: value})
    assert project(ProviderSearchResponse([item], public_notices=(PublicSearchNotice(public),)), item) is None


@pytest.mark.parametrize('change', [
    {'owned': True}, {'owned': 0}, {'downloadable': True}, {'downloadable': 0},
    {'download_url': 'https://example.org/book'}, {'source_account_id': ''},
    {'source_account_id': 'borrower-b'}, {'title': ''}, {'title': object()},
    {'description': 'https://example.org/?access_token=secret'},
    {'metadata': {'subjects': [{'account': 'b'}]}},
    {'metadata': {'subjects': [None]}}, {'metadata': {'subjects': ['x'] * 33}},
])
def test_contradictory_or_malformed_public_claim_is_closed(change):
    item = row()
    public = replace(item, **change)
    assert project(ProviderSearchResponse([item], public_notices=(PublicSearchNotice(public),)), item) is None


def test_duplicate_unknown_or_missing_envelope_is_closed():
    item = row()
    notice = PublicSearchNotice(item)
    for notices in ((notice, notice), (PublicSearchNotice(item, 2),),
                    (PublicSearchNotice(item, True),), (None,), [notice]):
        assert project(ProviderSearchResponse([item], public_notices=notices), item) is None


def test_same_coordinates_from_another_request_are_not_an_envelope_match():
    item = row()
    response = ProviderSearchResponse([item], public_notices=(PublicSearchNotice(item),))
    assert project(response, replace(item)) is None


def test_public_projection_never_copies_action_or_url_fields():
    item = row(thumbnail_url='https://example.org/cover',
               friendly_url='https://example.org/item', price='3', currency='EUR',
               revision_id='private-revision', metadata={'raw': object()})
    actual = project(ProviderSearchResponse([item], public_notices=(PublicSearchNotice(item),)), item)
    assert actual is not None
    assert all(getattr(actual, name) is None for name in (
        'thumbnail_url', 'friendly_url', 'price', 'currency', 'revision_id',
        'source_account_id', 'document_classification',
    ))


def test_hostile_capability_is_not_executed():
    class Hostile:
        def __getattribute__(self, name):
            raise AssertionError('provider code executed')
    item = row()
    assert project(Hostile(), item) is None
    assert project(ProviderSearchResponse([item], public_notices=(Hostile(),)), item) is None
    assert project(ProviderSearchResponse([item], public_notices=(PublicSearchNotice(Hostile()),)), item) is None


def test_hostile_authority_scalar_is_not_compared_or_executed():
    class Hostile:
        def __eq__(self, other):
            raise AssertionError('unexpected equality callback')
    item = row()
    for field in ('owned', 'downloadable'):
        public = replace(item, **{field: Hostile()})
        response = ProviderSearchResponse([item], public_notices=(PublicSearchNotice(public),))
        assert project(response, item) is None


@pytest.mark.parametrize('provider', ['synthetic-a', 'synthetic-b', 'unknown'])
def test_orchestrator_admits_only_current_response_public_notice(provider):
    private = row(provider, owned=True, source_account_id='borrower-b',
                  download_url='https://private.invalid/download')
    public = row(provider, metadata={'subjects': ['Fiction'], 'private': 'no'})
    session = SimpleNamespace(provider_id=provider, credential_id='source-b')
    response = ProviderSearchResponse(
        [private], public_notices=(PublicSearchNotice(public),),
    )
    admitted = _public_rows_from_response(response, session)
    assert len(admitted) == 1
    assert admitted[0].title == 'Public title'
    assert admitted[0].metadata == {
        'catalogue_access': 'shared_read_only', 'subjects': ['Fiction'],
    }
    assert admitted[0].source_account_id is None
    assert admitted[0].download_url is None
    assert admitted[0].owned is False
    assert _public_rows_from_response(
        ProviderSearchResponse([private]), session,
    ) == []
    assert _public_rows_from_response(
        ProviderSearchResponse([replace(private)],
                               public_notices=(PublicSearchNotice(public),)), session,
    ) == [admitted[0]]


def test_orchestrator_rejects_cross_provider_and_ambiguous_notices():
    session = SimpleNamespace(provider_id='synthetic-a', credential_id='source-b')
    item = row()
    other = row('synthetic-b')
    assert _public_rows_from_response(
        ProviderSearchResponse([other], public_notices=(PublicSearchNotice(other),)),
        session,
    ) == []
    foreign_credential = row('synthetic-a', 'credential-from-unrelated-session')
    assert _public_rows_from_response(
        ProviderSearchResponse(
            [foreign_credential],
            public_notices=(PublicSearchNotice(foreign_credential),),
        ), session,
    ) == []
    assert _public_rows_from_response(
        ProviderSearchResponse([item], public_notices=(
            PublicSearchNotice(item), PublicSearchNotice(item),
        )), session,
    ) == []
    assert _public_rows_from_response(
        ProviderSearchResponse([item, object()],
                               public_notices=(PublicSearchNotice(item),)), session,
    ) == []
    assert _public_rows_from_response(
        ProviderSearchResponse([item, item],
                               public_notices=(PublicSearchNotice(item),)), session,
    ) == []
    assert _public_rows_from_response(
        ProviderSearchResponse([item], public_notices=[PublicSearchNotice(item)]),
        session,
    ) == []


class _ResponseBoundSource:
    """Synthetic provider with separate private search and public response."""
    def __init__(self, provider, credential):
        self.provider_id = provider
        self.credential_id = credential
        self.connected = True
        self.capabilities = SimpleNamespace(search=True)
        self.descriptor = SimpleNamespace(display_name=provider)
        self.client = None
        self.last_search_warnings = []
        self.private_calls = 0
        self.response_calls = 0
        self.stream_calls = 0
        self.on_response = None
        self.public_notices = True

    def search(self, query, scope, *, field='all', content_types=()):
        self.private_calls += 1
        return [row(self.provider_id, self.credential_id, owned=True,
                    title='Progressive Book', source_account_id='owner-b',
                    download_url='https://private.invalid/token')]

    def search_with_response_facets(self, query, scope='all', *,
                                    field='all', content_types=()):
        self.response_calls += 1
        assert scope == 'catalog', 'foreign search must not query private scope'
        private = self.search(query, scope, field=field, content_types=content_types)[0]
        if self.on_response is not None:
            self.on_response()
        public = replace(private, title='Progressive Book',
                         owned=False, source_account_id=None,
                         download_url=None, metadata={'language': 'fr'})
        return ProviderSearchResponse(
            [private],
            public_notices=(PublicSearchNotice(public),) if self.public_notices else (),
        )

    def search_stream(self, *args, **kwargs):
        self.stream_calls += 1
        raise AssertionError('foreign streaming must be suppressed')


@pytest.mark.parametrize('provider', ['synthetic-a', 'synthetic-b', 'unknown'])
@pytest.mark.parametrize('progressive', [False, True])
def test_foreign_search_requires_current_response_and_preserves_private_owner(
    tmp_path, monkeypatch, provider, progressive,
):
    from tests.test_core043_progress_cache_stats import _service
    service = _service(tmp_path)
    session = _ResponseBoundSource(provider, 'credential-b')
    service._sessions = {provider: session}
    owners = {(provider, 'credential-b'): 'bob'}
    monkeypatch.setattr(service.storage, 'identity_for_connection',
                        lambda p, c: owners.get((p, c)))
    monkeypatch.setattr(service, '_mirror_remote_results', lambda *a, **kw: 0)

    def search(*, force):
        if progressive:
            batches = []
            summary = service.search_progressive(
                'Progressive Book', force=force, on_batch=batches.append,
            )
            assert not summary.cancelled
            assert len(batches) == 1
            return list(service.last_search)
        return service.search('Progressive Book', force=force)

    try:
        result = search(force=True)
        assert len(result) == 1
        assert result[0].source_provider == provider
        assert result[0].metadata == {
            'catalogue_access': 'shared_read_only', 'language': 'fr',
        }
        assert result[0].download_url is None and result[0].owned is False
        assert session.response_calls == 1
        assert session.stream_calls == 0
        assert search(force=False) == result
        assert session.response_calls == 1
        # Revocation/ownership recheck must not publish a stale response.
        session.on_response = lambda: owners.__setitem__((provider, 'credential-b'), 'alice')
        if progressive:
            summary = service.search_progressive('Progressive Book', force=True)
            assert summary.cancelled
        else:
            assert service.search('Progressive Book', force=True) == []
    finally:
        service.close()


@pytest.mark.parametrize('progressive', [False, True])
def test_foreign_legacy_response_without_public_notice_never_publishes(
    tmp_path, monkeypatch, progressive,
):
    from tests.test_core043_progress_cache_stats import _service
    service = _service(tmp_path)
    session = _ResponseBoundSource('synthetic-a', 'credential-b')
    session.public_notices = False
    service._sessions = {'synthetic-a': session}
    monkeypatch.setattr(service.storage, 'identity_for_connection',
                        lambda *_: 'bob')
    monkeypatch.setattr(service, '_mirror_remote_results', lambda *a, **kw: 0)
    try:
        if progressive:
            summary = service.search_progressive('Progressive Book', force=True)
            assert not summary.cancelled
            assert service.last_search == []
        else:
            assert service.search('Progressive Book', force=True) == []
        assert session.response_calls >= 1
        assert session.stream_calls == 0
    finally:
        service.close()


@pytest.mark.parametrize("provider", ["paris", "synthetic-a", "synthetic-b"])
def test_configured_primary_preference_is_generic_but_never_rights(
    tmp_path, provider,
):
    from tests.test_core043_progress_cache_stats import _service
    service = _service(tmp_path)
    try:
        session = SimpleNamespace(provider_id=provider, credential_id="source-a")
        service.config.set("identities", [{
            "id": "alice", "label": "Alice",
            f"primary_{provider}_connection_id": "source-a",
        }])
        hints = _configured_search_preferences(
            service, service.current_identity, [session],
        )
        assert hints == {provider: "source-a"}
        # A preference merely ranks already-owned credentials; it must not
        # turn an unrelated session into a publicly attested response.
        malformed = SimpleNamespace(provider_id="bad/name", credential_id="source-a")
        assert _configured_search_preferences(
            service, service.current_identity, [malformed],
        ) == {}
    finally:
        service.close()
