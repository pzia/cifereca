"""P1 #1867 — store purchases are not library loans or local EPUBs."""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.gui.sync_view import sync_source_presentation
from cifereca.gui.statistics_presentation_controller import (
    statistics_button_label, statistics_lines,
)


def _source(provider: str, *caps: str):
    return NS(
        provider_id=provider,
        label="Compte personnel", enabled=True, connected=True,
        sync_capable=True, status="idle", last_attempt="", last_success="",
        error="", sync_capabilities=caps,
    )


def _statistics(loans):
    return {
        "known_loans": loans,
        "local_books": 1,
        "local_placements": 1,
        "remote_owned_rights": 5,
        "loan_sources_observed": 0,
        "formats": {}, "sources": {}, "epub_covers": {},
    }


def test_numilog_personal_inventory_means_purchases_not_a_loan():
    source = sync_source_presentation(
        _source("numilog", "remote_inventory", "entitlements"),
    )
    assert source.action_label == "Actualiser les livres achetés Numilog"
    assert "inventaire des livres achetés" in source.summary
    assert "droits sur les livres possédés" in source.summary
    assert "aucun emprunt de bibliothèque créé" in source.summary
    assert "aucun fichier EPUB/ACSM téléchargé" in source.summary
    assert "droits et emprunts" not in source.summary


def test_kobo_owned_manifest_is_not_automatically_downloaded_or_loaned():
    source = sync_source_presentation(
        _source("kobo", "remote_inventory", "entitlements", "local_materialization"),
    )
    assert source.action_label == "Mettre à jour les notices Kobo en ligne"
    assert "inventaire des livres achetés" in source.summary
    assert "droits sur les livres possédés" in source.summary
    assert "ne télécharge aucun ebook" in source.summary
    assert "droits et emprunts" not in source.summary


def test_paris_stays_a_genuine_library_loan_with_all_loan_labels():
    source = sync_source_presentation(
        _source("paris", "remote_inventory", "entitlements"),
    )
    assert source.action_label == "Synchroniser Compte personnel"
    assert "droits et emprunts" in source.summary
    assert "inventaire distant" in source.summary
    assert "livres achetés" not in source.summary


def test_store_only_identity_does_not_claim_purchases_are_loans_or_files():
    stats = _statistics(0)
    lines = statistics_lines(stats)
    assert "Prêts de bibliothèque connus : 0" in lines
    assert "Droits d’achat distants actifs : 5" in lines
    assert "Livres locaux distincts : 1" in lines
    assert "les livres achetés en ligne ne sont pas des emprunts ni des fichiers locaux" in (
        " ".join(lines).lower()
    )
    assert statistics_button_label(stats) == "1 livres · 0 prêts de bibliothèque"


def test_unknown_loan_count_is_not_confused_with_zero_store_purchases():
    stats = _statistics(None)
    lines = statistics_lines(stats)
    assert "Prêts de bibliothèque connus : non actualisés" in lines
    assert statistics_button_label(stats) == "1 livres · — prêts de bibliothèque"
    assert "Droits d’achat distants actifs : 5" in lines
