"""#1346: personal Kobo inventory is not evidence of a downloadable file.

Synthetic provider-only holdings; no Kobo credentials, HTTP, DRM or disk I/O.
"""
from __future__ import annotations

import pytest

from tests.test_kobo_manifest_policy_p1 import FakeVault, session_with_vault


@pytest.mark.parametrize("archived,downloadable,status,label", (
    (True, True, "owned", "Dans votre bibliothèque Kobo · archivé · téléchargement ignoré"),
    (True, False, "owned", "Dans votre bibliothèque Kobo · archivé · téléchargement ignoré"),
    (False, True, "owned", "Dans votre bibliothèque Kobo · synchronisation locale disponible"),
    (False, False, "owned", "Dans votre bibliothèque Kobo · non téléchargeable"),
    (False, None, "owned", "Dans votre bibliothèque Kobo · téléchargement non vérifié"),
    (None, True, "owned", "Dans votre bibliothèque Kobo · synchronisation locale disponible"),
    (None, False, "owned", "Dans votre bibliothèque Kobo · non téléchargeable"),
    (None, None, "owned", "Dans votre bibliothèque Kobo · téléchargement non vérifié"),
    ("false", True, "attention_required", "Dans votre bibliothèque Kobo · état d’archive non vérifiable"),
    ("true", False, "attention_required", "Dans votre bibliothèque Kobo · état d’archive non vérifiable"),
    (0, True, "attention_required", "Dans votre bibliothèque Kobo · état d’archive non vérifiable"),
    (1, True, "attention_required", "Dans votre bibliothèque Kobo · état d’archive non vérifiable"),
    ([], True, "attention_required", "Dans votre bibliothèque Kobo · état d’archive non vérifiable"),
    ({}, True, "attention_required", "Dans votre bibliothèque Kobo · état d’archive non vérifiable"),
))
def test_owned_holdings_never_invent_archive_or_download_permission(
    archived, downloadable, status, label,
):
    session = session_with_vault(FakeVault([]))
    try:
        row = {"product_id": "PRIVATE_EDITION", "title": "Bearer PRIVATE_TITLE", "owned": True}
        if archived is not None:
            row["archived"] = archived
        if downloadable is not None:
            row["downloadable"] = downloadable
        record = session.normalize_item(row, default_collection="library")
        actual = session.holdings(record)
        assert actual.availability_status == status
        assert actual.availability_label == label
        assert "PRIVATE" not in actual.availability_label
        assert "Bearer" not in actual.availability_label
        assert actual.holdings == []
    finally:
        session.close()


@pytest.mark.parametrize("downloadable", (
    None, "false", "true", "0", 1, [], {},
))
def test_non_boolean_download_rights_remain_unverified(downloadable):
    session = session_with_vault(FakeVault([]))
    try:
        row = {"product_id": "edition-1", "owned": True}
        if downloadable is not None:
            row["downloadable"] = downloadable
        record = session.normalize_item(row, default_collection="library")
        actual = session.holdings(record)
        assert actual.availability_status == "owned"
        assert actual.availability_label == "Dans votre bibliothèque Kobo · téléchargement non vérifié"
        assert record.downloadable is None
    finally:
        session.close()


def test_public_catalogue_hit_never_inherits_personal_holdings():
    session = session_with_vault(FakeVault([]))
    try:
        book = session.normalize_item({
            "product_id": "public-edition",
            "title": "Public",
            "price": {"amount": "12.00", "currency": "EUR"},
        }, default_collection="catalog")
        assert book.owned is False
        detail = session.holdings(book)
        assert detail.availability_status == "catalog"
        assert detail.availability_label == "Disponible sur Kobo · 12.00 EUR"
    finally:
        session.close()



@pytest.mark.parametrize("archived", [True, False, None])
def test_kobo_archive_status_is_only_an_observed_delivery_fact(archived):
    """#514: archive is not a failure and unarchiving is not a permission grant."""
    session = session_with_vault(FakeVault([]))
    try:
        row = {"product_id": "same-book", "owned": True, "downloadable": True}
        if archived is not None:
            row["archived"] = archived
        detail = session.holdings(
            session.normalize_item(row, default_collection="library")
        )
        assert detail.availability_status == "owned"
        if archived is True:
            assert "archivé · téléchargement ignoré" in detail.availability_label
            assert "restaurer" not in detail.availability_label
        else:
            assert "synchronisation locale disponible" in detail.availability_label
        assert detail.holdings == []
    finally:
        session.close()
