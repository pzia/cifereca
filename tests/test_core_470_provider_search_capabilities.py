from __future__ import annotations

from types import SimpleNamespace

from cifereca.core.provider_search import provider_search_summary
from cifereca.providers import ProviderCapabilities, ProviderDescriptor, ProviderSearchCapabilities
from cifereca.services import LibraryService


def test_unspecified_search_contract_preserves_legacy_compatibility():
    contract = ProviderSearchCapabilities()

    assert contract.declared is False
    assert contract.supports_field('collection') is True
    assert contract.supports_scope('digital') is True
    assert contract.summary() == 'Capacités de recherche non déclarées'


def test_declared_search_contract_exposes_fields_scopes_and_pagination():
    contract = ProviderSearchCapabilities(
        fields=('all', 'title', 'author'),
        scopes=('all', 'digital'),
        pagination='page',
        page_size=50,
        max_pages=4,
    )

    assert contract.declared is True
    assert contract.supports_field('title') is True
    assert contract.supports_field('isbn') is False
    assert contract.supports_scope('digital') is True
    assert contract.supports_scope('physical') is False
    assert '50/page' in contract.summary()
    assert 'max 4 pages' in contract.summary()


def test_provider_descriptor_keeps_existing_positional_contract_and_search_contract_is_optional():
    descriptor = ProviderDescriptor(
        'demo', 'Demo', 'Description', 'ready', ProviderCapabilities(search=True)
    )

    assert descriptor.id == 'demo'
    assert descriptor.search_capabilities == ProviderSearchCapabilities()


def test_core_refuses_only_explicitly_unsupported_search_fields():
    descriptor = ProviderDescriptor(
        id='demo', display_name='Demo', description='Description', state='ready',
        capabilities=ProviderCapabilities(search=True),
        search_capabilities=ProviderSearchCapabilities(fields=('all', 'title'), scopes=('all', 'digital')),
    )
    session = SimpleNamespace(descriptor=descriptor, search_capabilities=descriptor.search_capabilities)

    assert LibraryService._search_contract_warning(session, 'title', 'digital') == ''
    assert 'champ de recherche non pris en charge' in LibraryService._search_contract_warning(session, 'isbn', 'digital')
    assert 'périmètre de recherche non pris en charge' in LibraryService._search_contract_warning(session, 'title', 'physical')


def test_search_capability_summary_for_one_provider_is_user_readable():
    summary = provider_search_summary({
        'gutenberg': ProviderSearchCapabilities(
            fields=('all', 'title', 'author'), pagination='next-link', page_size=50, max_pages=10
        )
    }, ('gutenberg',))

    assert 'Champs : Tous, Titre, Auteur' in summary
    assert 'Pagination : next-link' in summary
    assert '50/page' in summary
    assert 'max 10 pages' in summary


def test_search_capability_summary_for_multiple_providers_shows_common_fields_and_variable_paging():
    summary = provider_search_summary({
        'a': ProviderSearchCapabilities(fields=('all', 'title', 'author'), pagination='page'),
        'b': ProviderSearchCapabilities(fields=('all', 'title', 'isbn'), pagination='none'),
    }, ('a', 'b'))

    assert 'Champs communs : Tous, Titre' in summary
    assert 'Pagination variable selon la source' in summary


def test_search_capability_summary_stays_empty_when_nothing_is_declared():
    assert provider_search_summary({'legacy': ProviderSearchCapabilities()}, ('legacy',)) == ''


def test_query_controls_source_exposes_provider_capability_hint():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    controls = (root / 'src/cifereca/gui/query_controls.py').read_text(encoding='utf-8')
    window = (root / 'src/cifereca/gui/main_window.py').read_text(encoding='utf-8')
    factory = (root / 'src/cifereca/gui/query_widget_factory_controller.py').read_text(encoding='utf-8')
    assert 'query_widget_factory_controller.build_query_controls' in window

    assert 'def set_provider_search_capabilities' in controls
    assert 'provider_search_summary' in controls
    assert 'setter(window.service.provider_search_capabilities())' in factory
