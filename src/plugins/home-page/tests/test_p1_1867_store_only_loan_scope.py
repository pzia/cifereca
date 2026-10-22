"""P1 #1867: store entitlements are not missing library-loan observations.

Public dashboard fixtures only; no GTK, accounts, provider calls or file IO.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


MODULE = Path(__file__).resolve().parents[1] / "cifereca_home.py"
spec = importlib.util.spec_from_file_location("home_1867_scope", MODULE)
home = importlib.util.module_from_spec(spec)
spec.loader.exec_module(home)


def _row(providers, *, loans=None, observed=0, owned=3, local=0,
         capabilities=(), sources=None, sync=None):
    return {
        "providers": providers,
        "sources": (
            sources if sources is not None
            else [{"provider_id": name, "connected": True, "label": name}
                  for name in providers]
        ),
        "capabilities": list(capabilities),
        "statistics": {
            "local_books": local,
            "remote_owned_rights": owned,
            "known_loans": loans,
            "loan_sources_observed": observed,
            "sources": {"numilog": local},
        },
        "sync": sync if sync is not None else {},
    }


@pytest.mark.parametrize("providers,owned,local", [
    (["numilog"], 0, 0),
    (["numilog"], 1, 0),
    (["numilog"], 8, 3),
    (["kobo"], 12, 0),
    (["kobo", "numilog"], 17, 2),
    (["kobo", "gallica", "gutenberg", "ebooksgratuits"], 0, 4),
    (["numilog", "numilog-catalog"], 4, 0),
])
def test_only_stores_or_public_catalogues_have_no_loan_refresh_alert(
    providers, owned, local,
):
    row = _row(providers, owned=owned, local=local)
    assert home._loan_scope_not_configured(row)
    actions = home.attention_items(row, now=50_000)
    assert "loans-stale" not in {action["key"] for action in actions}
    summary = home.overview_counts_lines(row["statistics"], row=row)
    assert "aucune source de prêt configurée" in summary[0]
    assert "prêts de bibliothèque connus : 0" not in summary[0]
    assert f"Droits d’achat distants : {owned}" in summary[1]
    lines = home.statistics_lines(row)
    assert "Prêts de bibliothèque non actualisés" not in "\n".join(lines)
    assert "aucune source de prêt configurée" in lines[0]


@pytest.mark.parametrize("providers", [
    ["paris"], ["paris", "kobo"], ["numilog", "paris", "gutenberg"],
    ["my-next-library"], ["kobo", "unrecognized-provider"], [],
])
def test_loan_sources_unknown_sources_or_missing_inventory_keep_alert(providers):
    row = _row(providers)
    assert not home._loan_scope_not_configured(row)
    assert "loans-stale" in {item["key"] for item in home.attention_items(row, now=1)}
    assert "prêts de bibliothèque connus : —" in home.overview_counts_lines(
        row["statistics"], row=row,
    )[0]


@pytest.mark.parametrize("evidence", [
    {"loans": 1},
    {"loans": 4},
    {"loans": 0, "observed": 1},
    {"loans": None, "observed": 2},
    {"capabilities": ({"id": "loans", "label": "Emprunts"},)},
])
def test_explicit_loan_evidence_is_never_erased_by_store_provider_list(evidence):
    row = _row(["numilog", "kobo"], **evidence)
    assert not home._loan_scope_not_configured(row)
    assert "aucune source de prêt" not in home.overview_counts_lines(
        row["statistics"], row=row,
    )[0]


def test_mixed_profile_preserves_due_alert_and_loan_freshness():
    row = _row(["kobo", "numilog", "paris"], loans=2, observed=1, owned=9)
    row["statistics"].update(due_soon_loans=1, loans_observed_at=100)
    actions = home.attention_items(row, now=100 + 25 * 3600)
    assert {"loans-due", "loans-stale"} <= {action["key"] for action in actions}
    assert "prêts de bibliothèque connus : 2" in home.overview_counts_lines(
        row["statistics"], row=row,
    )[0]
    assert "Droits d’achat distants : 9" in home.overview_counts_lines(
        row["statistics"], row=row,
    )[1]


def test_store_sync_failures_still_raise_attention_and_never_claim_a_loan():
    row = _row(["numilog"], sync={"errors": 2, "attention_required": 1})
    actions = home.attention_items(row)
    assert "sync" in {action["key"] for action in actions}
    assert "loans-stale" not in {action["key"] for action in actions}


def test_statistics_origins_cannot_establish_a_configured_loan_provider():
    row = _row(["numilog"], local=5)
    row["statistics"]["sources"] = {"paris": 4, "local": 1}
    # A legacy file associated with Paris does NOT mean Paris is currently
    # a configured, connected source, or that a loan is due for refresh.
    assert home._loan_scope_not_configured(row)
    assert not any(item["key"] == "loans-stale"
                   for item in home.attention_items(row))


def test_untrusted_or_missing_provider_projection_must_not_suppress_alert():
    for providers in (None, "numilog", [], ["numilog", None],
                      ["numilog"] * 129, ["UNKNOWN_PRIVATE_PROVIDER"]):
        row = _row(["numilog"])
        row["providers"] = providers
        assert not home._loan_scope_not_configured(row)
    row = _row(["kobo"])
    row.pop("statistics")
    assert not home._loan_scope_not_configured(row)
    row = _row(["numilog"])
    row["statistics"]["known_loans"] = "private://secret/loans"
    # Invalid values may not be rendered as data or converted to a claimed
    # zero. Keep application scope based solely on provider registrations.
    assert "private://" not in repr(home.overview_counts_lines(
        row["statistics"], row=row,
    ))


def test_original_stats_only_utility_stays_backward_compatible():
    stats = {"local_books": 0, "remote_owned_rights": None, "known_loans": None}
    text = home.overview_counts_lines(stats)
    assert "prêts de bibliothèque connus : —" in text[0]
    assert "Droits d’achat distants : —" in text[1]
