from __future__ import annotations

import pytest

from cifereca_paris_client import ParisClient, ParisRedirectError


@pytest.fixture
def client():
    instance = ParisClient("https://bibliotheques.paris.fr")
    try:
        yield instance
    finally:
        instance.close()


@pytest.mark.parametrize(
    "url",
    [
        "https://acs4.edelivres.fr/delivery/signed-token",
        "https://acs4.edelivres.fr:443/delivery/signed-token",
        "https://acs4.edenlivres.fr/delivery/signed-token",
        "https://acs4.cantook.net/delivery/signed-token",
    ],
)
def test_observed_acs4_delivery_hosts_are_allowed_only_as_exact_https_hosts(client, url):
    client._validate_pnb_redirect_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://acs4.edelivres.fr/delivery/token",
        "https://acs4.edelivres.fr:444/delivery/token",
        "https://sub.acs4.edelivres.fr/delivery/token",
        "https://acs4.edelivres.fr.example.test/delivery/token",
        "https://acs5.edelivres.fr/delivery/token",
        "http://acs4.cantook.net/delivery/token",
        "https://acs4.cantook.net:8443/delivery/token",
        "https://sub.acs4.cantook.net/delivery/token",
        "https://user:secret@acs4.cantook.net/delivery/token",
    ],
)
def test_acs4_policy_rejects_http_non_443_neighbors_and_embedded_credentials(client, url):
    with pytest.raises(ParisRedirectError):
        client._validate_pnb_redirect_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://acs4.edelivres.fr/private/signed/path?token=very-secret",
        "https://acs4.edenlivres.fr/private/signed/path?token=very-secret",
        "https://acs4.cantook.net/private/signed/path?token=very-secret",
    ],
)
def test_acs4_trace_paths_never_expose_signed_delivery_paths_or_query(client, url):
    safe = client._pnb_trace_path(url)

    assert safe == "/<redacted>"
    assert "private" not in safe
    assert "very-secret" not in safe


def test_rejected_acs4_redirect_error_never_echoes_signed_path_or_query(client):
    url = "https://acs4.cantook.net:8443/private/signed/path?token=very-secret"

    with pytest.raises(ParisRedirectError) as exc_info:
        client._validate_pnb_redirect_url(url)

    message = str(exc_info.value)
    assert "acs4.cantook.net" in message
    assert "private" not in message
    assert "very-secret" not in message
