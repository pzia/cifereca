from cifereca.plugin_api import redact_text


def test_public_redact_text_preserves_original_url_query():
    value = "https://example.test/file?x=VALUE"
    assert redact_text(value) == value


def test_legacy_redact_does_not_modify_nested_login_or_password():
    from cifereca.diagnostics import redact

    original = {
        "login": "alice@example.test",
        "password": "ORIGINAL_PASSWORD",
        "token": "ORIGINAL_TOKEN",
        "file_path": "/home/alice/book.epub",
    }
    assert redact(original) is original
    assert redact(original)["password"] == "ORIGINAL_PASSWORD"
