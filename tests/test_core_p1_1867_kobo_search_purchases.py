"""#3196: supplier-owned catalogue flags never authorize a legacy Kobo GET."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui.query_model import result_row
from cifereca.services import LibraryService
from cifereca.core.search_orchestration import availability_display_label
from tests.test_core_1337_kobo_personal_manifest_rights import _row, _service


def test_kobo_legacy_direct_download_denied_without_provider_lookup(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        lookups = Mock(side_effect=AssertionError("legacy lookup forbidden"))
        monkeypatch.setattr(service, "_session_for_source", lookups)
        item = replace(_row(), downloadable=True, owned=True)
        assert result_row(item)["state"] == "remote_catalog"
        assert LibraryService.can_direct_download(service, item) is False
        lookups.assert_not_called()
    finally:
        service.close()


def test_kobo_availability_is_observation_not_purchase():
    item = replace(_row(), downloadable=True, owned=True)
    service = NS(can_direct_download=Mock(side_effect=AssertionError("not a permission")))
    label = availability_display_label(service, item, NS(availability_label="Empruntable"))
    assert "vérifier" in label
    assert "achat" not in label.lower()
    assert "posséd" not in label.lower()
    service.can_direct_download.assert_not_called()


def test_kobo_offline_catalogue_never_advertises_current_delivery():
    item = replace(_row(), metadata={"search_provenance": "known_catalog_stale"})
    label = availability_display_label(NS(), item, NS(availability_label="Disponible"))
    assert "hors ligne" in label


@pytest.mark.parametrize("provider", [
    "kobo", "numilog", "paris", "synthetic-a", "synthetic-b", "unknown",
])
def test_foreign_public_notice_never_advertises_actionable_availability(provider):
    item = replace(
        _row(), source_provider=provider, owned=False, downloadable=False,
        metadata={"catalogue_access": "shared_read_only"},
    )
    service = NS(can_direct_download=Mock(
        side_effect=AssertionError("no rights lookup for a public notice"),
    ))
    label = availability_display_label(
        service, item, NS(
            availability_label="Disponible à l’emprunt",
            availability_status="available",
        ),
    )
    assert "vérifier" in label
    assert "emprunt" not in label.casefold()
    assert "téléchargement" not in label.casefold()
    service.can_direct_download.assert_not_called()


@pytest.mark.parametrize("provider", ["paris", "synthetic-a", "unknown"])
def test_stale_public_notice_does_not_become_current_availability(provider):
    item = replace(
        _row(), source_provider=provider,
        metadata={
            "catalogue_access": "shared_read_only",
            "search_provenance": "known_catalog_stale",
        },
    )
    service = NS(can_direct_download=Mock(
        side_effect=AssertionError("no rights lookup for a public notice"),
    ))
    label = availability_display_label(
        service, item, NS(availability_label="Disponible"),
    )
    assert "hors ligne" in label
    service.can_direct_download.assert_not_called()


@pytest.mark.parametrize("provider", [
    "kobo", "numilog", "paris", "synthetic-alpha", "unknown",
])
@pytest.mark.parametrize("stale", [False, True])
def test_private_inventory_observation_is_not_a_delivery_right(provider, stale):
    item = replace(
        _row(), source_provider=provider, source_collection="library",
        downloadable=True, owned=True,
        metadata={"search_provenance": "known_catalog_stale"} if stale else {},
    )
    service = NS(can_direct_download=Mock(
        side_effect=AssertionError("inventory is not delivery evidence"),
    ))
    label = availability_display_label(
        service, item, NS(
            availability_label="Téléchargement direct",
            availability_status="available",
        ),
    )
    assert "hors ligne" in label if stale else "vérifier" in label
    assert "Téléchargement direct" not in label
    service.can_direct_download.assert_not_called()
