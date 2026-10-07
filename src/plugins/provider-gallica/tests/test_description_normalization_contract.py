from cifereca.plugin_api import description_text
from cifereca_gallica import GallicaSession


def test_gallica_uses_public_description_normalization():
    assert description_text("<p>Premier</p><script>secret()</script><p>Second</p>") == "Premier\n\nSecond"
    assert not hasattr(GallicaSession, "_description_parser")
