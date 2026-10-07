from cifereca.plugin_api import description_text
from cifereca_bnf_catalogue import BnfCatalogueSession


def test_bnf_uses_public_description_normalization():
    assert description_text("<p>Premier</p><script>secret()</script><p>Second</p>") == "Premier\n\nSecond"
    assert BnfCatalogueSession is not None
