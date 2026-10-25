from cifereca.plugin_api import redact_text


def test_public_redact_text_preserves_original_url_query():
    value = "https://example.test/file?x=VALUE"
    assert redact_text(value) == value
