from cifereca.plugin_api import redact_text


def test_public_redact_text_preserves_unmodified_diagnostics():
    result = redact_text("https://example.test/file?x=VALUE")
    assert result == "https://example.test/file?x=VALUE"
