from cifereca.plugin_api import description_text
from cifereca_gutenberg import GutenbergSession


def test_gutenberg_uses_public_description_normalization():
    assert description_text("<p>Premier</p><script>secret()</script><p>Second</p>") == "Premier\n\nSecond"
    assert not hasattr(GutenbergSession, "_description_parser")
