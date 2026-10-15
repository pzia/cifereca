"""#1599: neighbour cards use the same public-text boundary as DetailPanel.

Pure headless projection; no GTK, network access, file, account or acquisition.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from constellations_page import neighbour_card_fields, neighbour_count_label


class Poison:
    def __str__(self):
        raise AssertionError("Bearer PRIVATE_ACCOUNT")

    def __bool__(self):
        raise AssertionError("Bearer PRIVATE_TOKEN")


def _edge(*, title="Book", authors=(), access=(), reasons=()):
    return NS(
        target=NS(book=NS(title=title, authors=authors), availability=access),
        reasons=reasons,
    )


def test_untrusted_neighbour_metadata_cannot_crash_or_leak_into_cards():
    edge = _edge(
        title=Poison(),
        authors=(Poison(), "Alice", "PRIVATE_AUTHOR\u202e", "Bob"),
        access=(Poison(), "externe", "PRIVATE_RIGHT\u200b"),
        reasons=(Poison(), "Inféré : Sujet : forêt",
                 "Auteur : proche", "PRIVATE_REASON\nTOKEN"),
    )
    card = neighbour_card_fields(edge)
    assert card == {
        "title": "",
        "authors": "Alice, Bob",
        "access": "externe",
        "reason": "Auteur : proche",
        "other_reasons": 1,
        "inferred": True,
    }
    assert neighbour_count_label((edge,)) == (
        "1 voisin(s) proposé(s) · 1 avec un indice inféré"
    )
    assert "PRIVATE" not in repr(card)


def test_only_poisoned_reasons_yield_sanitized_unknown_reason():
    edge = _edge(
        title="Visible", authors={"PRIVATE_KEY": "Alice"},
        access={"PRIVATE_RIGHT": "possédé"},
        reasons=(Poison(), "PRIVATE\x7fTOKEN"),
    )
    card = neighbour_card_fields(edge)
    assert card["title"] == "Visible"
    assert card["authors"] == ""
    assert card["access"] == ""
    assert card["reason"] == "Raison inconnue"
    assert card["other_reasons"] == 0
    assert card["inferred"] is False
    assert neighbour_count_label((edge,)).startswith("1 voisin(s) proposé(s) · 0")


def test_valid_inferred_and_attested_reasons_remain_distinct_and_ordered():
    edge = _edge(
        title="Public", authors=("Alice", "Bob"), access=("empruntable",),
        reasons=("Inféré : Projet : voyage", "Série : Cycle",
                 "Auteur : Alice"),
    )
    card = neighbour_card_fields(edge)
    assert card["reason"] == "Série : Cycle"
    assert card["other_reasons"] == 2
    assert card["inferred"] is True
    assert card["authors"] == "Alice, Bob"
    assert card["access"] == "empruntable"


def test_long_unbounded_reason_iterable_is_never_exhausted():
    class Endless:
        def __iter__(self):
            for _ in range(32):
                yield "Auteur : connu"
            raise AssertionError("More than 32 reasons must not be read")

    edge = _edge(reasons=Endless())
    card = neighbour_card_fields(edge)
    assert card["reason"] == "Auteur : connu"
    assert card["other_reasons"] == 31
    assert neighbour_count_label((edge,)) == (
        "1 voisin(s) proposé(s) · 0 avec un indice inféré"
    )


def test_center_heading_uses_same_trust_boundary_as_detail_panel(monkeypatch):
    """Malformed catalogue titles never enter the GTK heading unfiltered."""
    from constellations_page import View, constellation_detail_presentation

    class PoisonTitle:
        def __str__(self):
            raise AssertionError("Bearer PRIVATE_TITLE /home/reader")

    monkeypatch.setattr("constellations_page.neighbours", lambda *a, **kw: ())
    for title, expected in (
        ("Titre attesté", "Titre attesté"),
        ("PRIVATE_TITLE\u202e", ""),
        ("PRIVATE_TITLE\nBearer", ""),
        ("X" * 2049, ""),
        (PoisonTitle(), ""),
    ):
        headings, details, neighbours = [], [], []
        view = object.__new__(View)
        view.center = NS(
            book=NS(title=title, authors=()),
            sources=(), availability=(), url="",
        )
        view.flow = NS(get_first_child=lambda: None, append=lambda widget: None)
        view.heading = NS(set_label=headings.append)
        view.neighbours_summary = NS(set_label=neighbours.append)
        view.master = NS(show_detail=details.append, empty_detail=lambda: None)
        view.Gtk = NS(Label=lambda **kwargs: kwargs)
        view.nodes, view.local = (), ()
        view.render()

        assert headings == ["Centre : " + expected]
        assert len(details) == 1
        assert details[0].title == expected
        assert "PRIVATE" not in str(headings + details) if expected == "" else True
