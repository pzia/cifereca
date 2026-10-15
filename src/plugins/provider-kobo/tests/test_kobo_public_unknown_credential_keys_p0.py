"""#1346: a public Kobo catalogue never exports renamed credential fields.

All payloads are synthetic. These are pure projections without Vault HTTP.
"""
from __future__ import annotations

import json

from cifereca_kobo_vault import KoboVaultClient


def _payload():
    return {
        "page": 1,
        "total": 1,
        "metadata": {
            "publisher": "Public House",
            "partner_refresh_token": "PRIVATE_REFRESH",
            "api-key": "PRIVATE_API_KEY",
            "signedDownload": "https://PRIVATE/download",
        },
        "items": [{
            "product_id": "public-edition",
            "title": "Public book",
            "authors": ["Public Author"],
            "price": {"amount": "9.99", "currency": "EUR"},
            "collection": "library",
            "owned": True,
            "acquisitionLink": "https://PRIVATE/acquisition",
            "customApiKey": "PRIVATE_API_KEY",
            "secret_key": "PRIVATE_SECRET",
            "partnerSecret": "PRIVATE_SECRET",
            "userPassword": "PRIVATE_PASSWORD",
            "sessionId": "PRIVATE_SESSION",
            "Cookie": "PRIVATE_COOKIE",
            "session_key": "PRIVATE_SESSION_KEY",
            "auth": {"bearer": "PRIVATE_BEARER"},
            "metadata": {
                "publicVersion": "2",
                "language": "fr",
                "partner_access_token": "PRIVATE_ACCESS_TOKEN",
                "signedLicense": "PRIVATE_LICENSE",
                "extra": [
                    {"genre": "fiction", "client-secret": "PRIVATE_CLIENT"},
                    {"publisher": "Public House", "localFileUri": "PRIVATE_PATH"},
                ],
            },
        }],
    }


def test_public_catalogue_envelope_and_nested_book_secrets_are_filtered():
    projected = KoboVaultClient._public_catalog_payload(_payload())
    row = projected["items"][0]
    assert projected["page"] == projected["total"] == 1
    assert projected["metadata"] == {"publisher": "Public House"}
    assert row["title"] == "Public book"
    assert row["authors"] == ["Public Author"]
    assert row["price"] == {"amount": "9.99", "currency": "EUR"}
    assert row["owned"] is False
    assert row["collection"] == "catalog"
    assert row["metadata"] == {
        "publicVersion": "2",
        "language": "fr",
        "extra": [
            {"genre": "fiction"},
            {"publisher": "Public House"},
        ],
    }
    public = json.dumps(projected)
    for secret in (
        "PRIVATE", "partner_access_token", "customApiKey", "secret_key",
        "partnerSecret", "userPassword", "sessionId", "Cookie",
        "signedLicense", "signedDownload", "acquisitionLink",
    ):
        assert secret not in public


def test_direct_public_book_hit_does_not_inherit_per_record_credentials():
    row = KoboVaultClient._public_catalog_hit({
        "product_id": "public", "title": "Visible",
        "owned": True, "downloadable": True,
        "metadata": {
            "public": "kept",
            "clientId": "PRIVATE_CLIENT",
            "bearer": "PRIVATE_BEARER",
            "acquisition_url": "PRIVATE_ACQUISITION",
            "downloadLink": "PRIVATE_DOWNLOAD",
        },
    })
    assert row == {
        "product_id": "public", "title": "Visible",
        "metadata": {"public": "kept"},
        "owned": False, "collection": "catalog",
    }


def test_unknown_credential_suffixes_do_not_survive_nested_collections():
    row = KoboVaultClient._public_catalog_hit({
        "product_id": "public",
        "nested": [
            {"bibliography": "visible", "accessKey": "PRIVATE_ACCESS"},
            {"details": {
                "author": "Visible",
                "sessionToken": "PRIVATE_SESSION",
                "thirdPartyCredential": "PRIVATE_CREDENTIAL",
                "deviceApiKey": "PRIVATE_DEVICE_KEY",
            }},
        ],
    })
    assert row["nested"] == [
        {"bibliography": "visible"},
        {"details": {"author": "Visible"}},
    ]
    assert "PRIVATE" not in json.dumps(row)


def test_non_string_private_key_objects_are_not_rendered():
    class Poison:
        def __str__(self):
            raise RuntimeError("Bearer PRIVATE_TOKEN")

        def __repr__(self):
            raise RuntimeError("Bearer PRIVATE_TOKEN")

    result = KoboVaultClient._strip_public_private_fields({
        "title": "Safe",
        Poison(): "PRIVATE_SECRET",
    })
    assert result == {"title": "Safe"}
