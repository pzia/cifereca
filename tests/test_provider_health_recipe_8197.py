from types import SimpleNamespace
from unittest.mock import Mock

from cifereca.core import provider_health_recipe as recipe
from cifereca.plugin_api.capture_vault import CaptureScope


def test_recipe_no_consent_or_unknown_scope_never_runs_readers(monkeypatch):
    loans = Mock(side_effect=AssertionError("unexpected loan reader"))
    rights = Mock(side_effect=AssertionError("unexpected rights reader"))
    monkeypatch.setattr(recipe, "read_loan_inventory", loans)
    monkeypatch.setattr(recipe, "read_account_rights_snapshot", rights)
    service = SimpleNamespace(_provider_health=object())
    scope = CaptureScope("fixture", "i", "a", "owner")
    assert all(row["reason"] == "confirmation_required"
               for row in recipe.run_recipe(service, scope, (), confirmed=False))
    monkeypatch.setattr(recipe, "recipe_options", lambda service: ())
    assert all(row["reason"] == "scope_denied"
               for row in recipe.run_recipe(service, scope, (), confirmed=True))
    loans.assert_not_called()
    rights.assert_not_called()


def test_recipe_options_no_health_returns_empty():
    assert recipe.recipe_options(SimpleNamespace()) == ()


def test_recipe_search_only_accepts_exact_positive_typed_response():
    from cifereca.models import SearchResult
    from cifereca.plugin_api import ProviderSearchResponse
    from dataclasses import replace
    scope = CaptureScope("fixture", "i", "a", "owner")
    row = SearchResult(id="id", record_id="id", docbase="db", medium="unknown",
                       title="Book", author=None, type=None, format=None,
                       publisher=None, published_at=None, description=None,
                       thumbnail_url=None, friendly_url=None, scenario="search",
                       source_provider="fixture", source_credential="i", source_account_id="a")
    assert recipe._qualified_search_outcome(ProviderSearchResponse([row]), scope) == ("success", (row,))
    assert recipe._qualified_search_outcome(ProviderSearchResponse([], terminal_empty=True), scope) == ("unknown", ())
    assert recipe._qualified_search_outcome(ProviderSearchResponse([row], partial=True), scope) == ("unknown", ())
    other = replace(row, source_account_id="other")
    assert recipe._qualified_search_outcome(ProviderSearchResponse([row, other]), scope) == ("unknown", ())
    assert recipe._qualified_search_outcome([row], scope) == ("unknown", ())
