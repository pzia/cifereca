"""#4552: render independently witnessed age bounds without selecting a false value."""
from dataclasses import replace

from cifereca.core.notice_c_public_age import PublicAgeObservation, PublicAgeRange
from cifereca.core.notice_c_catalogue_views import project_public_catalogue_view
from cifereca.gui.notice_c_public_catalogue_widget import render_public_catalogue_view
from tests.test_core_p1_2385_catalogue_views import _page


def test_native_public_ages_are_visible_only_in_source_and_detail_views():
    page = _page()
    age = PublicAgeObservation(
        provider_id="provider.example", record_id="record:1",
        docbase="docbase:1", medium="digital", source_revision="rev:1",
        source_field="provider.example.age",
        ranges=(PublicAgeRange(9, None, "reading", "Dès 9 ans"),
                PublicAgeRange(8, 12, "recommended", "8–12 ans")),
    )
    first = replace(page.hits[0], age_observation=age)
    second = replace(page.matching_sources[1], age_observation=None)
    page = replace(page, hits=(first,), matching_sources=(first, second))
    detail = project_public_catalogue_view(page, mode="detail")
    source = project_public_catalogue_view(page, mode="sources")
    assert detail.rows[0].age_observation is age
    assert [row.age_observation for row in source.rows] == [age, None]
    for mode in ("list", "grid", "compact", "timeline"):
        assert all(row.age_observation is None
                   for row in project_public_catalogue_view(page, mode=mode).rows)

    class Widget:
        def __init__(self, **kwargs):
            self.label = kwargs.get("label", "")
            self.children = []
        def append(self, child):
            self.children.append(child)
        def set_xalign(self, value):
            pass
        def set_wrap(self, value):
            pass

    class Gtk:
        Box = Label = Widget
        class Orientation:
            VERTICAL = 1

    root = render_public_catalogue_view(source, Gtk=Gtk)
    labels = [child.label for card in root.children for child in card.children]
    assert any("À partir de 9 ans" in value and "Dès 9 ans" in value
               for value in labels)
    assert any("De 8 à 12 ans" in value and "8–12 ans" in value
               for value in labels)
    assert sum("Âge déclaré" in value for value in labels) == 2
    assert all("Emprunter" not in value and "Possédé" not in value
               for value in labels)
