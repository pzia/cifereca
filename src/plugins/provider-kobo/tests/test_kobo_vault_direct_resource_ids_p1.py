"""#1346: no private Kobo HTTP operation from an unverifiable edition key.

Tests use only a synthetic Vault transport. No account access, acquisition,
files, DRM or network is involved.
"""
from __future__ import annotations

from urllib.parse import quote

import httpx
import pytest

from cifereca.plugin_api import ProviderError
from cifereca_kobo_vault import KoboVaultClient


@pytest.mark.parametrize("invalid", [
    None, "", " ", " leading", "trailing ", "line\nPRIVATE_TOKEN",
    "line\rPRIVATE_TOKEN", "line\tPRIVATE_TOKEN",
    "invisible\u202ePRIVATE_TOKEN", "bidi\u2066PRIVATE_TOKEN",
    "x" * 257, 0, 123, True, False, [], {}, ["id"],
])
@pytest.mark.parametrize("operation", ["library_item", "acquisition"])
def test_private_resource_lookup_rejects_bad_identity_before_any_http(
    invalid, operation,
):
    seen = []

    def handler(request):
        seen.append(request.url)
        pytest.fail("invalid product key must never reach Vault")

    client = KoboVaultClient(
        "https://vault.example", "PRIVATE_ACCESS_TOKEN",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(ProviderError, match="identifiant de produit non vérifiable") as error:
            getattr(client, operation)(invalid)
        for private in ("PRIVATE", "Bearer", "leading", "trailing", "bidi"):
            assert private not in str(error.value)
        assert seen == []
    finally:
        client.close()


@pytest.mark.parametrize("product", [
    "edition-123", "urn:uuid:abc-def", "édition-123",
    "edition/part?token=not-private", "id+variant:2",
])
def test_valid_identifiers_are_encoded_and_request_the_expected_resource(product):
    seen = []

    def handler(request):
        seen.append((request.method, request.url.raw_path.decode("ascii")))
        if request.method == "GET":
            return httpx.Response(200, json={"item": {
                "product_id": product, "title": "Catalogue detail",
            }})
        if request.method == "POST":
            return httpx.Response(200, json={"acquisition": {"candidates": []}})
        pytest.fail("unexpected method")

    client = KoboVaultClient(
        "https://vault.example", "fixture-token",
        transport=httpx.MockTransport(handler),
    )
    try:
        assert client.library_item(product)["product_id"] == product
        assert client.acquisition(product, mode="descriptor") == {
            "acquisition": {"candidates": []},
        }
        escaped = quote(product, safe="")
        assert seen == [
            ("GET", f"/api/v2/library/{escaped}"),
            ("POST", f"/api/v2/library/{escaped}/acquisition"),
        ]
    finally:
        client.close()


@pytest.mark.parametrize("argument,secret", [
    ({"mode": "Bearer PRIVATE_SECRET"}, "PRIVATE_SECRET"),
    ({"display_profile": "Desktop?token=PRIVATE_SECRET"}, "PRIVATE_SECRET"),
])
def test_invalid_acquisition_parameters_never_echo_private_values_or_send_http(
    argument, secret,
):
    seen = []

    def handler(request):
        seen.append(request.url)
        pytest.fail("invalid acquisition parameters must not reach Vault")

    client = KoboVaultClient(
        "https://vault.example", "PRIVATE_ACCESS_TOKEN",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(ProviderError) as error:
            client.acquisition("edition-123", **argument)
        assert secret not in str(error.value)
        assert "PRIVATE" not in str(error.value)
        assert seen == []
    finally:
        client.close()
