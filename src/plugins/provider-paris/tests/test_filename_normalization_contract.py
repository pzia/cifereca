from cifereca.plugin_api import sanitize_filename_component


def test_paris_filename_uses_shared_public_policy():
    assert sanitize_filename_component("Titre: été?/") == "Titre_ été"
    assert sanitize_filename_component("???", "download") == "download"
