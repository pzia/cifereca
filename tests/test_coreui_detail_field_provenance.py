"""The neutral detail retains declared per-field sources without new rights."""
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.detail_field_provenance import annotate_detail_field, detail_field_provenance
from cifereca.plugin_api import DetailField, Provenance
from tests.test_core_0940_detail_actions import _group, Button


def field(source, *, value="Même valeur", observed="2026-11-19T10:00:00Z"):
    return DetailField("field", "Auteur", value, "author", Provenance(
        source=source, provider_id="public-source", source_id="edition-42",
        observed_at=observed, metadata={"ignored": "private-free-form"},
    ))


def test_each_value_keeps_its_source_without_fabricating_an_attestation_or_timestamp():
    a, b = field("Source A"), field("Source B", observed="")
    assert "Source du champ : Source A" in detail_field_provenance(a)
    assert "Référence déclarée : edition-42" in detail_field_provenance(a)
    assert "Observation déclarée : 2026-11-19T10:00:00Z" in detail_field_provenance(a)
    assert "Source du champ : Source B" in detail_field_provenance(b)
    assert "Observation" not in detail_field_provenance(b)
    assert "attesté" not in detail_field_provenance(a)
    assert "private-free-form" not in detail_field_provenance(a)


def test_legacy_missing_empty_and_malformed_provenance_stay_distinct():
    assert detail_field_provenance(DetailField("id", "Langue", "fr")) == ""
    assert detail_field_provenance(NS(provenance={"source": "untyped"})) == ""
    assert detail_field_provenance(NS(provenance=Provenance(""))) == "Provenance non renseignée"
    assert detail_field_provenance(NS(provenance=Provenance("https://source.test/?token=SECRET"))) == "Provenance non renseignée"


def test_tooltip_and_accessible_description_include_the_complete_value_and_source():
    written = {}
    row = NS(set_tooltip_text=lambda text: written.update(tooltip=text),
             update_property=lambda keys, values: written.update(description=values[0]))
    annotate_detail_field(row, "Auteur", "Valeur complète", field("Source A"), description_property="description")
    assert written["tooltip"] == written["description"]
    assert written["tooltip"].startswith("Auteur : Valeur complète\nSource du champ : Source A")


def test_legacy_tooltip_is_unchanged_and_does_not_assign_a_new_accessible_description():
    written = {}
    row = NS(set_tooltip_text=lambda text: written.update(tooltip=text),
             update_property=lambda *_: pytest.fail("legacy field acquired provenance"))
    annotate_detail_field(row, "Auteur", "Valeur complète", None, description_property="description")
    assert written == {"tooltip": "Auteur : Valeur complète"}


@pytest.mark.parametrize("section,actions", [("Détail", True), ("Catalogue", True),
                                             ("Essentiel", True), ("Contexte", False), ("Avis · source", False)])
def test_real_group_keeps_same_value_from_two_sources_and_respects_passive_sections(section, actions):
    render, _clipboard = _group()
    groups = []
    host = NS(window=NS(service=NS(current_identity=NS(identity_id="alice")),
                        search_books=lambda *a, **kw: pytest.fail("render initiated a search")),
              _append_content=groups.append)
    fields = (field("Source A"), field("Source B"))
    render(host, section, [(f.label, f.value) for f in fields], fields=fields, field_actions=actions)

    def walk(widget):
        yield widget
        for child in widget.children:
            yield from walk(child)

    rows = [w for w in walk(groups[0]) if getattr(w, "title", None) == "Auteur"]
    assert len(rows) == 2
    assert "Source A" in rows[0].tooltip and "Source B" not in rows[0].tooltip
    assert "Source B" in rows[1].tooltip and "Source A" not in rows[1].tooltip
    assert [sum(isinstance(w, Button) for w in walk(row)) for row in rows] == ([2, 2] if actions else [0, 0])
