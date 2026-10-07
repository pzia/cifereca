from cifereca.plugin_api import description_text
from cifereca_kobo_catalog import KoboCatalogService


def test_kobo_uses_public_description_normalization():
    assert description_text("<p>Premier</p><script>secret()</script><p>Second</p>") == "Premier\n\nSecond"
    assert KoboCatalogService is not None
