"""#1599: result explanations and provider labels must be safe in list and detail.

Pure in-memory selection with no GTK, provider, account or acquisition.
"""
from __future__ import annotations

from recommendation_engine import RankedCandidate
from recommendations import _public_ranked_values
from tests.test_recommendation_detail_projection_contract_p1 import _make_view


class Poison:
    def __str__(self):
        raise AssertionError("PRIVATE_SIGNED_AUTH must not cross the host")


class SubclassedSecret(str):
    pass


def _sections(detail):
    return {s.id: s for s in detail.sections}


def test_untrusted_ranked_reasons_and_sources_are_isolated_in_both_surfaces():
    view, rows, details, _ = _make_view({
        "title": "Livre public", "authors": ("Alice",),
    })
    view._provider_labels = {
        "kobo": "Kobo", "bnf": "BnF", "poison": Poison(),
    }
    view._ranked = [RankedCandidate(
        item={"title": "Livre public"}, score=84,
        reasons=("Auteur commun", Poison(), "PRIVATE\nACCESS", SubclassedSecret("PRIVATE_BIDI"),
                 "Thème attesté"),
        sources=("kobo", Poison(), {"PRIVATE_TOKEN": "bnf"}, "bnf",
                 SubclassedSecret("PRIVATE_SOURCE"), "poison", "kobo"),
    )]
    view._render(preserve_summary=True)
    assert len(rows) == 1
    assert "Kobo · BnF" in rows[0].meta
    assert "Pourquoi : Auteur commun · Thème attesté" in rows[0].meta
    assert "PRIVATE" not in rows[0].meta
    view._selected_row(rows[0])
    catalogue = _sections(details[-1])["catalogue"]
    assert [(f.label, f.value) for f in catalogue.fields if f.label == "Source"] == [
        ("Source", "Kobo"), ("Source", "BnF"),
    ]
    recommendation = _sections(details[-1])["recommendation"]
    assert next(f.value for f in recommendation.fields if f.id == "reasons") == (
        "Auteur commun · Thème attesté"
    )
    assert "PRIVATE" not in repr((rows[0].meta, catalogue.fields, recommendation.fields))


def test_mapping_and_string_subclass_are_not_iterated_as_explanation_fields():
    assert _public_ranked_values({"PRIVATE_FIELD": "not a reason"}) == ()
    assert _public_ranked_values(SubclassedSecret("PRIVATE_CHARACTERS")) == ()
    view, rows, details, _ = _make_view({"title": "No rights"})
    view._ranked = [RankedCandidate(
        item={"title": "No rights"}, score=81,
        sources={"PRIVATE_ORIGIN": "kobo"}, reasons={"PRIVATE_REASON": "cached"},
    )]
    view._render(preserve_summary=True)
    assert rows[0].meta == ""
    view._selected_row(rows[0])
    assert _sections(details[-1])["recommendation"].fields[-1].value == (
        "Plusieurs signaux concordants"
    )
    assert "catalogue" not in _sections(details[-1])


def test_legitimate_multiple_labels_are_bounded_without_fake_values():
    long_reasons = tuple(f"Indice {i}" for i in range(90))
    long_sources = tuple(f"provider-{i}" for i in range(90))
    view, rows, details, _ = _make_view({"title": "Avec notices"})
    view._ranked = [RankedCandidate(
        item={"title": "Avec notices"}, score=71,
        sources=long_sources, reasons=long_reasons,
    )]
    view._render(preserve_summary=True)
    assert "provider-31" in rows[0].meta
    assert "provider-32" not in rows[0].meta
    view._selected_row(rows[0])
    catalogue = _sections(details[-1])["catalogue"]
    assert len([f for f in catalogue.fields if f.label == "Source"]) == 32
    recommendation = _sections(details[-1])["recommendation"]
    reasons = next(f.value for f in recommendation.fields if f.id == "reasons")
    assert "Indice 31" in reasons and "Indice 32" not in reasons
