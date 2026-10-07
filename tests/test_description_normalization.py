import xml.etree.ElementTree as ET

from cifereca.plugin_api import description_element, description_text


def test_description_text_preserves_blocks_and_hides_active_content():
    value = "<p>Premier <b>texte</b></p><ul><li>Un</li><li>Deux</li></ul><script>secret()</script>"
    assert description_text(value) == "Premier texte\n\n• Un\n• Deux"


def test_description_text_decodes_fully_escaped_markup_once():
    assert description_text("&lt;p&gt;Bonjour &amp;amp; salut&lt;/p&gt;") == "Bonjour & salut"


def test_description_element_handles_nested_xhtml():
    node = ET.fromstring('<content xmlns="urn:test"><p>Alpha</p><p>Beta</p></content>')
    assert description_element(node) == "Alpha\n\nBeta"
