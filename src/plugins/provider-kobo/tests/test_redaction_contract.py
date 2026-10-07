import httpx
import pytest

from cifereca.plugin_api import redact_text
from cifereca_kobo_vault import KoboVaultClient, KoboVaultError


def test_public_redact_text_hides_url_query():
    value = "https://example.test/path?x=VALUE"
    redacted = redact_text(value)
    assert "VALUE" not in redacted
    assert redacted == "https://example.test/<redacted>"


def test_kobo_vault_errors_use_public_redaction():
    def handler(request):
        return httpx.Response(
            400,
            json={"message": "https://example.test/path?x=VALUE"},
            request=request,
        )

    client = KoboVaultClient(
        "https://vault.example.test",
        "fixture-token",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(KoboVaultError) as caught:
            client.account()
        message = str(caught.value)
        assert "VALUE" not in message
        assert "https://example.test/<redacted>" in message
    finally:
        client.close()
