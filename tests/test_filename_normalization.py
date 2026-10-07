from cifereca.plugin_api import sanitize_filename_component


def test_sanitize_filename_component_preserves_existing_provider_policy():
    assert sanitize_filename_component("Titre: été?/") == "Titre_ été"
    assert sanitize_filename_component("  ._Titre._  ") == "Titre"
    assert sanitize_filename_component("???", "fallback") == "fallback"
    assert sanitize_filename_component("???", "") == ""
