"""#1346: malformed private acquisition options never reach Kobo Vault.

All requests are mocked locally; no DRM or materialization is triggered.
"""
from __future__ import annotations

import json

import httpx
import pytest

from cifereca.plugin_api import ProviderError
from cifereca_kobo_vault import KoboVaultClient


def _client():
    posted = []

    def handler(request):
        posted.append(request)
        assert request.method == "POST"
        assert request.url.path == "/api/v2/library/book-1/acquisition"
        return httpx.Response(200, json={"acquisition": {"candidates": []}})

    return KoboVaultClient(
        "https://vault.example", "PRIVATE_TOKEN",
        transport=httpx.MockTransport(handler),
    ), posted


@pytest.mark.parametrize("bad", [
    "false", "true", "0", "1", "Bearer PRIVATE_TOKEN",
    0, 1, [], {}, False + 1,
])
def test_include_acsm_is_a_json_boolean_not_truthy_coercion(bad):
    client, posted = _client()
    try:
        with pytest.raises(ProviderError, match="option ACSM invalide") as error:
            client.acquisition("book-1", include_acsm=bad)
        assert "PRIVATE" not in str(error.value)
        assert posted == []
    finally:
        client.close()


@pytest.mark.parametrize("revision", [
    False, True, 0, 12, [], {}, ["PRIVATE_ID"],
    " ", " leading", "trailing ", "line\nPRIVATE_TOKEN",
    "line\tPRIVATE_TOKEN", "line\rPRIVATE_TOKEN",
    "hidden\u202ePRIVATE", "x" * 257,
])
def test_invalid_revision_rejected_before_private_http(revision):
    client, posted = _client()
    try:
        with pytest.raises(ProviderError, match="identifiant de révision invalide") as error:
            client.acquisition("book-1", revision_id=revision)
        assert "PRIVATE" not in str(error.value)
        assert posted == []
    finally:
        client.close()


@pytest.mark.parametrize("mode", [
    None, 0, False, [], {}, "Bearer PRIVATE_TOKEN", "delegated/PRIVATE",
    "descriptor\nPRIVATE",
])
def test_invalid_mode_rejected_without_echo_or_http(mode):
    client, posted = _client()
    try:
        with pytest.raises(ProviderError, match="mode d'acquisition invalide") as error:
            client.acquisition("book-1", mode=mode)
        assert "PRIVATE" not in str(error.value)
        assert posted == []
    finally:
        client.close()


@pytest.mark.parametrize("display_profile", [
    None, "Desktop", "Android", " Desktop ",
])
@pytest.mark.parametrize("include_acsm", [None, True, False])
def test_valid_parameters_preserve_exact_json_booleans(display_profile, include_acsm):
    client, posted = _client()
    try:
        assert client.acquisition(
            "book-1", mode="DESCRIPTOR",
            revision_id="revision-1", display_profile=display_profile,
            include_acsm=include_acsm,
        )["acquisition"]["candidates"] == []
        assert len(posted) == 1
        body = json.loads(posted[0].content)
        assert body["revision_id"] == "revision-1"
        assert body["mode"] == "descriptor"
        if display_profile is None:
            assert "display_profile" not in body
        else:
            assert body["display_profile"] == display_profile.strip()
        if include_acsm is None:
            assert "include_acsm" not in body
        else:
            assert body["include_acsm"] is include_acsm
    finally:
        client.close()


def test_absent_empty_revision_is_omitted_but_valid_unicode_key_is_preserved():
    client, posted = _client()
    try:
        client.acquisition("book-1", revision_id="")
        client.acquisition("book-1", revision_id=None)
        client.acquisition("book-1", revision_id="révision-été")
        assert all("revision_id" not in json.loads(req.content) for req in posted[:2])
        assert json.loads(posted[2].content)["revision_id"] == "révision-été"
    finally:
        client.close()
