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
