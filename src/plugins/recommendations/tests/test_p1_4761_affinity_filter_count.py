"""P1 #4761: explicit affinity view threshold appears in shared result count.

A score used to hide a row is not a provider capability or ranking change.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from recommendations import UxRecommendationsView


class Label:
    def __init__(self):
        self.text = ""

    def set_label(self, value):
        self.text = value

    def get_label(self):
        return self.text


def fixture(default=15):
    view = object.__new__(UxRecommendationsView)
    selected = [default]
    scores = (94, 85, 60)
    view._ranked = list(scores)
    view._matches = lambda score: score >= selected[0]
    view._view_filters = lambda: ("", (), ())
    view._query_controls = None
    view._summary = Label()
    view._partial_warning_count = 0
    view._affinity_initial_threshold = default
    view._score = NS(get_value=lambda: selected[0])
    return view, selected


def test_default_is_not_repeated_as_active_local_filter():
    view, selected = fixture()
    view._update_view_summary(provisional=False)
    assert view._summary.text == "3 recommandation(s) · Ordre diversifié"
    selected[0] = 95
    view._update_view_summary(provisional=False)
    assert view._summary.text == "0 sur 3 · Affinité ≥ 95 · Ordre diversifié"
    selected[0] = 85
    view._update_view_summary(provisional=False)
    assert view._summary.text == "2 sur 3 · Affinité ≥ 85 · Ordre diversifié"
    selected[0] = 15
    view._update_view_summary(provisional=False)
    assert view._summary.text == "3 recommandation(s) · Ordre diversifié"


def test_custom_configured_baseline_not_misreported_as_user_filter():
    view, selected = fixture(85)
    view._update_view_summary(provisional=True)
    assert "Affinité ≥" not in view._summary.text
    assert "Provisoire" in view._summary.text
    selected[0] = 60
    view._update_view_summary(provisional=False)
    assert view._summary.text == "3 sur 3 · Affinité ≥ 60 · Ordre diversifié"


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), 101, -1, "SECRET", True])
def test_malformed_score_is_not_disclosed_as_a_filter_or_provider_fact(invalid):
    view, selected = fixture()
    selected[0] = invalid
    view._matches = lambda _score: True
    view._update_view_summary(provisional=False)
    assert view._summary.text == "3 recommandation(s) · Ordre diversifié"

def test_gtk_render_refreshes_filtered_summary_before_materializing_rows():
    """#774/#3687: list replacement may synchronously trigger GTK detail callbacks.

    The visible summary must already agree with the filtered projection when
    show_rows receives that projection, even if the following detail repaint
    is interrupted. No GTK, provider access or network is required.
    """
    view, _selected = fixture()
    selected_title = ["Titre B"]
    view._ranked = [
        NS(item={"title": "Titre A", "author": "Autrice"}, sources=("paris",),
           reasons=(), score=90),
        NS(item={"title": "Titre B", "author": "Autrice"}, sources=("paris",),
           reasons=(), score=85),
    ]
    view._matches = lambda ranked: ranked.item["title"] == selected_title[0]
    view._candidate_key = lambda ranked: ranked.item["title"]
    view._display_source = lambda source: "Paris" if source == "paris" else ""
    view._provisional_active = False
    view._exact_notice_state = None
    view._detail_selection_generation = 0
    view._reset_llm_selection = lambda _key: None
    observed = []
    view._master = NS(
        show_rows=lambda rows, total_count: observed.append(
            (view._summary.text, tuple(row.title for row in rows), total_count)
        ),
        set_list_state=lambda *_args: None,
        empty_detail=lambda *_args: None,
    )

    view._render()
    assert observed == [("1 sur 2 · Ordre diversifié", ("Titre B",), 2)]
    selected_title[0] = "Absent"
    view._render()
    assert observed[-1] == ("0 sur 2 · Ordre diversifié", (), 2)



def refresh_fixture():
    view, selected = fixture()
    view._clear_overview_refresh = lambda: None
    view.selected_identity_id = lambda: "alice"
    view._active_identity_id = "alice"
    view._provisional_order = {}
    view._set_provisional = lambda _value: None
    view._set_catalogue_coverage = lambda: None
    view._diagnostic_tooltip = lambda: "Synthétique"
    view._progress = NS(set_tooltip_text=lambda _value: None)
    view._master = NS(set_list_state=lambda *_args: None,
                      empty_detail=lambda *_args: None)
    view._start_refresh = lambda: None
    view._busy = False
    return view, selected


def test_refresh_preserves_exact_filter_count_and_attested_affinity_without_network():
    """#774: a delayed local refresh must not replace a correct filtered count."""
    view, selected = refresh_fixture()
    selected[0] = 95  # Zero visible, three already ranked
    view._summary.set_label("Ancien état non synchronisé")
    view.refresh()
    assert "0 sur 3 · Affinité ≥ 95" in view._summary.get_label()
    assert "Recalcul en cours" in view._summary.get_label()
    assert view._ranked == [94, 85, 60]
    view.refresh()
    assert view._summary.get_label().count("Recalcul en cours") == 1


def test_refresh_preserves_rich_unfiltered_summary_while_old_rows_remain():
    view, _selected = refresh_fixture()
    rich_summary = "3 recommandations à partir de 2 livres et 2 signaux"
    view._summary.set_label(rich_summary)
    view.refresh()
    assert view._summary.get_label().startswith(rich_summary + " · ")
    assert view._summary.get_label().count("Recalcul en cours") == 1
