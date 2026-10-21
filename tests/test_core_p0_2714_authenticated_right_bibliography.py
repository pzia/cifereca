"""P0 #2714 — authentic purchased-book descriptions outrank catalogue hints.

Purely synthetic descriptive metadata; never proof of download, account/file
association, or rights outside the already scoped entitlement.
"""
import pytest
from cifereca.core.library_local_inspection import _right_bibliography


def _right(provider="numilog", source="numilog.authenticated_library",
           **overrides):
    return {
        "provider_id": provider, "attested_source": source,
        "display_title": "Titre acheté authentifié",
        "display_authors": "Auteur acheté authentifié",
        "display_media_format": "epub",
        "item_id": "PRIVATE_EDITION_2714",
        "state": "active", **overrides,
    }


def _notice(**overrides):
    return (
        0, {"display_title": "Titre catalogue divergent",
            "display_authors": "Auteur catalogue divergent",
            **overrides}
    )


@pytest.mark.parametrize("provider,source", [
    ("numilog", "numilog.authenticated_library"),
    ("kobo", "kobo.personal_manifest"),
])
def test_attested_purchase_title_and_author_outrank_different_catalogue(provider, source):
    title, authors, details = _right_bibliography(
        _right(provider, source), [_notice()]
    )
    assert title == "Titre acheté authentifié"
    assert authors == "Auteur acheté authentifié"
    assert "Titre catalogue divergent" not in details
    assert "Auteur catalogue divergent" not in details
    assert "Origine du titre : bibliothèque authentifiée" in details
    assert "Origine des auteurs : bibliothèque authentifiée" in details
    assert "description, pas preuve de fichier" in details
    assert "acquisition, placement, SHA et structure" in details


def test_missing_purchased_author_falls_back_to_single_exact_catalogue_notice():
    title, authors, details = _right_bibliography(
        _right(display_authors=""), [_notice()]
    )
    assert title == "Titre acheté authentifié"
    assert authors == "Auteur catalogue divergent"
    assert "Origine des auteurs : notice de catalogue locale" in details
    assert "Origine du titre : bibliothèque authentifiée" in details


@pytest.mark.parametrize("matches,expected", [
    ([], "Aucune notice locale de cette édition exacte."),
    ([_notice(), (1, {"display_title": "Autre titre", "display_authors": "Autre"})],
     "Plusieurs notices locales pour la même édition"),
])
def test_missing_or_ambiguous_catalogue_never_hides_attested_purchase(matches, expected):
    title, authors, details = _right_bibliography(_right(), matches)
    assert title == "Titre acheté authentifié"
    assert authors == "Auteur acheté authentifié"
    assert expected in details
    assert "acquisition, placement, SHA et structure" in details


def test_unattested_right_keeps_single_catalogue_notice_preference():
    title, authors, details = _right_bibliography(
        _right(source="numilog.public_catalog"), [_notice()]
    )
    assert title == "Titre catalogue divergent"
    assert authors == "Auteur catalogue divergent"
    assert "non probante" in details
    assert "bibliothèque authentifiée" not in details


def test_unsupported_provider_cannot_borrow_another_providers_attestation():
    title, authors, details = _right_bibliography(
        _right(provider="paris"), [_notice()]
    )
    assert title == "Titre catalogue divergent"
    assert authors == "Auteur catalogue divergent"
    assert "non probante" in details


def test_unsafe_metadata_values_cannot_become_provenance_or_inferred_ownership():
    # The edition identifier must not contain the attack marker: this test
    # validates hostile *descriptive* metadata, not a private but legitimate
    # identifier on a private CoreUI inspection screen.
    right = _right(
        item_id="edition-synthétique-2714",
        display_title={"private": "value"},
        display_authors=None,
        display_media_format={"private": "format"},
    )
    title, authors, details = _right_bibliography(right, [_notice()])
    assert title == "Titre catalogue divergent"
    assert authors == "Auteur catalogue divergent"
    assert "private" not in details.lower()
    assert "Format déclaré : Non renseigné" in details
    assert "preuve de fichier" in details
