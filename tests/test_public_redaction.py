from cifereca.plugin_api import redact_text


def test_legacy_redact_text_keeps_original_url_and_query():
    value = "https://example.test/file?x=VALUE"
    assert redact_text(value) == value
