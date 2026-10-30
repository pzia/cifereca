from cifereca.language import normalize_language_tag, language_label
from cifereca.gui.navigation_history import NavigationHistory, NavigationState
from cifereca.core.search import filter_loaded_results


def test_language_aliases_and_filtering():
    assert normalize_language_tag('FR') == 'fr'
    assert normalize_language_tag('fre') == 'fr'
    assert normalize_language_tag('fra') == 'fr'
    assert normalize_language_tag('pt_br') == 'pt-BR'
    assert language_label('fr') == 'Français'
    rows = [
        {'title':'A','metadata':{'languages':['fre']}},
        {'title':'B','metadata':{'languages':['en']}},
    ]
    assert [r['title'] for r in filter_loaded_results(rows, 'fr', field='language')] == ['A']


def test_navigation_history_back_forward_and_branching():
    h = NavigationHistory()
    a = NavigationState('library', 'a', 'q1')
    b = NavigationState('search', 'a', 'q2')
    c = NavigationState('history', 'a', None)
    assert h.visit(a)
    assert h.visit(b)
    assert h.back() == a
    assert h.can_forward()
    assert h.forward() == b
    assert h.back() == a
    assert h.visit(c)
    assert not h.can_forward()



def test_contract_surface_is_added_only_in_iteration2():
    from cifereca.plugin_api import PLUGIN_API_VERSION
    from cifereca.plugins.categories import CORE_CATEGORIES
    assert PLUGIN_API_VERSION == "1.4.3"
    assert "maintenance.tool" in {row.id for row in CORE_CATEGORIES}
