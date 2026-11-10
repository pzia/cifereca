"""P0 #5056: bibliographic description priority is provider-neutral and read-only."""
import pytest

from cifereca.core.library_local_inspection import _right_bibliography


@pytest.mark.parametrize("provider,source", [
    ("kobo", "kobo.personal_manifest"),
    ("numilog", "numilog.authenticated_library"),
    ("synthetic-a", "synthetic-a.authenticated_library"),
    ("synthetic-b", "synthetic-b.personal_manifest"),
])
def test_exact_provider_provenance_prefers_bounded_right_description(provider, source):
    right = {
        "provider_id": provider,
        "attested_source": source,
        "item_id": "edition",
        "entitlement_type": "owned",
        "display_title": "Recorded title",
        "display_authors": "Recorded author",
    }
    notice = {"display_title": "Public title", "display_authors": "Public author"}
    title, authors, explanation = _right_bibliography(right, [("notice", notice)])
    assert title == "Recorded title"
    assert authors == "Recorded author"
    assert "bibliothèque authentifiée" in explanation
    assert "Ce bloc ne rattache aucun EPUB" in explanation


@pytest.mark.parametrize("provider,source", [
    ("unknown", "kobo.personal_manifest"),
    ("synthetic-a", "synthetic-b.authenticated_library"),
    ("synthetic-a", "synthetic-a.unrecognized_claim"),
    ("synthetic-a", ""),
    ("synthetic-a", None),
    (" synthetic-a", " synthetic-a.authenticated_library"),
    ("synthetic-a", ["synthetic-a.personal_manifest"]),
])
def test_invalid_or_foreign_provenance_does_not_override_catalogue(provider, source):
    right = {
        "provider_id": provider,
        "attested_source": source,
        "item_id": "edition",
        "entitlement_type": "owned",
        "display_title": "Recorded title",
        "display_authors": "Recorded author",
    }
    notice = {"display_title": "Public title", "display_authors": "Public author"}
    title, authors, explanation = _right_bibliography(right, [("notice", notice)])
    assert title == "Public title"
    assert authors == "Public author"
    assert "description du droit enregistré" not in explanation


def test_ambiguous_catalogue_never_promotes_unmatched_notice():
    right = {
        "provider_id": "unknown",
        "attested_source": "unknown.authenticated_library",
        "item_id": "edition",
        "entitlement_type": "loan",
        "display_title": "Recorded title",
    }
    notice = {"display_title": "Public title"}
    title, _, explanation = _right_bibliography(right, [("one", notice), ("two", notice)])
    assert title == "Recorded title"
    assert "Plusieurs notices locales" in explanation
    assert "Ce bloc ne rattache aucun EPUB" in explanation
