"""Shared detail labels preserve the availability evidence and its limits."""
from dataclasses import replace

import pytest

from cifereca.gui.detail_model import (
    catalogue_availability_label,
    compact_availability_label,
    search_detail_borrow_action_candidate,
)
from tests.test_p1_4276_search_document_filter import notice


@pytest.mark.parametrize("observed", [
    "Indisponible pour cet ouvrage dans le catalogue actuel",
    "Non disponible à l’emprunt pour le compte sélectionné",
    "Pas de téléchargement libre pour cette édition",
    "Disponible à l’emprunt — compte non vérifié",
    "Disponible à l’emprunt sous réserve de connexion",
    "Disponible dans un autre établissement uniquement",
    "Disponible hier ; état actuel inconnu",
    "Dans votre bibliothèque — fichier local non vérifié",
    "Téléchargement libre — offre à revalider pour ce compte",
    "Disponibilité non vérifiée — dernière observation hors ligne",
    "Notice publique · disponibilité à vérifier auprès du fournisseur",
    "Notice d’inventaire · disponibilité à vérifier auprès du fournisseur",
    "Catalogue partagé · droits non consultés",
    "Statut contradictoire — disponible et indisponible selon les sources",
    "État inconnu — cette notice ne constitue aucune preuve de possession",
])
def test_qualified_and_negative_availability_is_preserved(observed):
    compact = compact_availability_label(observed)
    assert compact == observed
    item = replace(notice("book", medium="digital"), metadata={})
    assert catalogue_availability_label(item, compact) == observed
    assert not search_detail_borrow_action_candidate(item, compact)


@pytest.mark.parametrize("observed,expected", [
    (None, ""), ("  ", ""), ("—", "—"),
    (" Disponibilité non vérifiée ", "Non vérifiée"),
    ("Disponibilite non verifiee", "Non vérifiée"),
    ("Dans votre bibliothèque", "Dans la bibliothèque"),
    ("Dans votre bibliotheque", "Dans la bibliothèque"),
    ("Téléchargement libre", "Téléchargement libre"),
    ("Telechargement libre", "Téléchargement libre"),
    ("Disponible à l’emprunt", "Empruntable"),
    ("Disponible à l'emprunt", "Empruntable"),
    ("DISPONIBLE A L'EMPRUNT", "Empruntable"),
    ("Réservée", "Réservée"),
])
def test_only_complete_known_labels_are_shortened(observed, expected):
    assert compact_availability_label(observed) == expected


def test_normalization_is_idempotent_and_keeps_catalogue_scope():
    item = notice("book", medium="digital")
    compact = compact_availability_label("Disponible à l’emprunt")
    displayed = catalogue_availability_label(item, compact)
    assert displayed == "Empruntable selon le catalogue · compte non vérifié"
    assert compact_availability_label(displayed) == displayed
