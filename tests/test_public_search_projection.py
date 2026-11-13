from dataclasses import replace
from types import SimpleNamespace

import pytest

from cifereca.models import SearchResult
from cifereca.plugin_api import ProviderSearchResponse, PublicSearchNotice
from cifereca.core.public_search_projection import project_public_search_result
from cifereca.core.search_orchestration import _public_rows_from_response


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
        ProviderSearchResponse([item], public_notices=[PublicSearchNotice(item)]),
        session,
    ) == []
