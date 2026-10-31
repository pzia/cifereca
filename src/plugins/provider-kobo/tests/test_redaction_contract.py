import httpx
import pytest

from cifereca.plugin_api import redact_text
from cifereca_kobo_vault import KoboVaultClient, KoboVaultError


def test_public_redact_text_preserves_original_url_query():
    value = "https://example.test/path?x=VALUE"
    redacted = redact_text(value)
    assert redacted == value


@pytest.mark.parametrize(
    ("status", "provider_code", "expected_code"),
    [
        (400, "signed-secret", "http_error"),
        (401, "unauthorized", "invalid_token"),
        (403, "forbidden", "forbidden"),
        (403, "insufficient_scope", "insufficient_scope"),
        (409, "archived_book", "archived_book"),
        (409, "signed-secret", "conflict"),
        (429, "rate_limited", "rate_limited"),
        (502, "signed-secret", "server_error"),
    ],
)
def test_vault_http_errors_use_a_strict_public_vocabulary(
    status, provider_code, expected_code
):
    secret = "UNIQUE_PRIVATE_TOKEN_VALUE"
    url = "https://example.test/private/path?token=" + secret

    def handler(request):
        return httpx.Response(
            status,
            json={
                "error": {
                    "code": provider_code,
                    "message": f"{secret} {url} private/account/path",
                },
                "message": f"account name: {secret}",
            },
            headers={"retry-after": "private/account/path " + secret},
            request=request,
        )

    client = KoboVaultClient(
        "https://vault.example.test",
        secret,
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(KoboVaultError) as caught:
            client.account()
        error = caught.value
        assert error.status_code == status
        assert error.code == expected_code
        assert secret not in str(error)
        assert url not in str(error)
        assert "private/account/path" not in str(error)
        assert provider_code == expected_code or provider_code not in str(error)
        if expected_code == "archived_book":
            assert "téléchargement ignoré" in str(error)
            assert "restaur" not in str(error).casefold()
            assert "réessayer" not in str(error).casefold()
    finally:
        client.close()


def test_vault_403_never_echoes_arbitrary_provider_code_or_message():
    def handler(request):
        return httpx.Response(
            403,
            json={
                "error": {
                    "code": "forbidden?token=PRIVATE_DATA",
                    "message": "Scope requis: kobo.acquisition.delegate; private account=PRIVATE_DATA",
                },
            },
            request=request,
        )

    client = KoboVaultClient(
        "https://vault.example.test",
        "fixture-token",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(KoboVaultError) as caught:
            client.acquisition("synthetic", mode="delegated")
        assert caught.value.code == "forbidden"
        assert caught.value.status_code == 403
        assert "PRIVATE_DATA" not in str(caught.value)
    finally:
        client.close()
