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


def test_availability_never_infers_from_empty_or_wrong_source():
    from cifereca.models import ItemDetail, Holding
    from dataclasses import replace
    item = SimpleNamespace(record_id="id", docbase="db", source_provider="fixture", source_credential="i")
    holding = Holding("h", None, None, None, None, "available", "available",
                      True, True, False, 0, None)
    detail = ItemDetail("id", "db", "Book", None, None, None, None,
                        "available", "available", [holding], "fixture", "i")
    assert recipe._availability_outcome(detail, item) == "success"
    assert recipe._availability_outcome(replace(detail, holdings=[]), item) == "unknown"
    assert recipe._availability_outcome(replace(detail, source_credential="other"), item) == "unknown"
    assert recipe._availability_outcome(replace(detail, availability_status="unknown"), item) == "unknown"


def test_quota_only_new_exact_typed_snapshot():
    from cifereca.plugin_api.lending_borrower_eligibility import BorrowerEligibilitySnapshot
    from cifereca.plugin_api.lending_capabilities import BorrowerOperationEvidence
    from dataclasses import replace
    scope = CaptureScope("fixture", "i", "a", "owner")
    row = BorrowerOperationEvidence("owner", "fixture", "i", "a", "db",
                                    "loan.borrow", remaining_loans=0)
    snapshot = BorrowerEligibilitySnapshot(100, 1, (row,))
    def check(value, previous=None, branches=frozenset({"db"})):
        return recipe._quota_read_outcome(value, previous, scope, 1, 100,
                                          branches, now=101)
    assert check(snapshot) == ("success", "quota_exhausted")
    assert check(snapshot, snapshot)[0] == "unknown"
    assert check(replace(snapshot, observed_at=99))[0] == "unknown"
    assert check(replace(snapshot, account_evidence=(row, row)))[0] == "unknown"
    assert check(replace(snapshot, account_evidence=(replace(row, account_id="other"),)))[0] == "unknown"
    assert check(replace(snapshot, account_evidence=(replace(row, remaining_loans=True),)))[0] == "unknown"
    assert check(snapshot, branches=frozenset({"db", "other"}))[0] == "unknown"
