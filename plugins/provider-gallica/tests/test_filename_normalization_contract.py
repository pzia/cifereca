from cifereca_gallica import GallicaSession


def test_gallica_filename_uses_public_sanitizer_contract():
    assert GallicaSession._safe_name("Titre: été?/") == "Titre_ été"
    assert GallicaSession._safe_name("???", "") == ""
