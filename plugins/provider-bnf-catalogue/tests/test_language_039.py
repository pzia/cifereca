from cifereca.plugin_api import normalize_language_tag

def test_language_contract_examples():
    assert normalize_language_tag('FR')=='fr'
    assert normalize_language_tag('fre')=='fr'
    assert normalize_language_tag('fra')=='fr'
