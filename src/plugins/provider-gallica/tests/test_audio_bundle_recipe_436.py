from __future__ import annotations

from pathlib import Path

from cifereca.plugin_api import (
    BundleAcquisitionRecipe,
    SearchResult,
    load_bundle_manifest,
)
from cifereca_gallica import _gallica_audio_recipe


def audio_item():
    return SearchResult(
        id="gallica:bpt6k123:audiobook",
        record_id="ark:/12148/bpt6k123",
        docbase="GALLICA",
        medium="digital",
        title="Audio",
        author="Auteur",
        type="Livre audio",
        format=None,
        publisher=None,
        published_at=None,
        description=None,
        thumbnail_url=None,
        friendly_url="https://gallica.bnf.fr/ark:/12148/bpt6k123",
        scenario="SRU-AUDIO",
        source_provider="gallica",
        source_credential="public",
        download_url="https://gallica.bnf.fr/ark:/12148/bpt6k123",
        content_type="audiobook",
        metadata={},
    )


def test_audio_recipe_uses_ark_only_as_resolver_and_keeps_tracks_unresolved():
    recipe = _gallica_audio_recipe(audio_item())

    assert isinstance(recipe, BundleAcquisitionRecipe)
    assert recipe.resolver_url == "https://gallica.bnf.fr/ark:/12148/bpt6k123"
    assert recipe.tracks == ()
    offer = recipe.to_offer()
    assert offer.source_kind == "bundle"
    assert offer.format == ""
    assert offer.source["resolved"] is False
    assert offer.source["tracks"] == []


def test_audio_recipe_requires_ark():
    row = audio_item()
    row.record_id = "missing"
    row.friendly_url = None
    row.download_url = None
    assert _gallica_audio_recipe(row) is None
