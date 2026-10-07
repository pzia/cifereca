from cifereca_gutenberg import GutenbergSession


def test_gutenberg_filename_uses_public_sanitizer_contract():
    assert GutenbergSession._safe_name("Titre: été?/") == "Titre_ été"
    assert GutenbergSession._safe_name("???", "gutenberg") == "gutenberg"
