"""#1599: public Recommendation detail never serializes untrusted scores.

Only in-memory headless rank selection. No GTK, provider, login or acquisition.
"""
from types import SimpleNamespace as NS
import pytest

from recommendation_engine import RankedCandidate
from recommendations import _public_affinity_score, UxRecommendationsView
from tests.test_recommendation_detail_projection_contract_p1 import _make_view


class PoisonScore:
    def __str__(self):
        raise AssertionError("PRIVATE_SIGNED_ACCOUNT_URL")
    def __lt__(self, _):
        raise AssertionError("PRIVATE_SIGNED_ACCOUNT_URL")


class SubclassScore(int):
    pass


@pytest.mark.parametrize("invalid", [
    PoisonScore(), True, False, SubclassScore(75), 75.0,
    "75", -1, 101, None, {"score": "PRIVATE_ACCOUNT"},
])
def test_invalid_score_is_never_converted_to_public_badge_or_affinity(invalid):
    view, rows, details, _ = _make_view({"title": "Source attestée"})
    view._ranked = [RankedCandidate(
        item={"title": "Source attestée"}, score=invalid,
        reasons=("Source attestée",), sources=("kobo",),
    )]
    view._render(preserve_summary=True)
    view._selected_row(rows[0])
    assert rows[0].badge == ""
    detail = details[0]
    assert all(not badge.startswith("Affinité") for badge in detail.badges)
    recommendation = next(s for s in detail.sections if s.id == "recommendation")
    affinity = next(f for f in recommendation.fields if f.id == "affinity")
    assert affinity.value == "Non attestée"
    assert "PRIVATE" not in repr((rows[0].badge, detail.badges, affinity.value))


def test_untrusted_score_is_filtered_before_comparison_with_minimum():
    view, _, _, _ = _make_view({"title": "Source attestée"})
    view._score = NS(get_value=lambda: 20)
    view.selected_provider_id = lambda: ""
    view.selected_language_id = lambda: ""
    view._text = NS(get_text=lambda: "")
    invalid = RankedCandidate(item={"title": "Source"}, score=PoisonScore(), reasons=(), sources=())
    assert view._matches(invalid) is False


@pytest.mark.parametrize("score", [0, 20, 77, 100])
def test_valid_integer_scores_remain_unchanged(score):
    assert _public_affinity_score(score) == score
    view, rows, details, _ = _make_view({"title": "Source attestée"})
    view._ranked = [RankedCandidate(
        item={"title": "Source attestée"}, score=score,
        reasons=("Source attestée",), sources=("kobo",),
    )]
    view._render(preserve_summary=True)
    view._selected_row(rows[0])
    assert rows[0].badge == f"Affinité {score}/100"
    assert f"Affinité {score}/100" in details[0].badges
    section = next(s for s in details[0].sections if s.id == "recommendation")
    assert next(f for f in section.fields if f.id == "affinity").value == f"{score}/100"
