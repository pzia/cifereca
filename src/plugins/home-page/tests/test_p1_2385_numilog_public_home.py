"""P1 #1867/#2385: public Numilog is independent, anonymous, and optional."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


module = Path(__file__).resolve().parents[1] / "cifereca_home.py"
spec = importlib.util.spec_from_file_location("home_2385_numilog_public", module)
home = importlib.util.module_from_spec(spec)
spec.loader.exec_module(home)


def _source(provider, *, connected=True):
    return {"provider_id": provider,
            "label": ("Numilog — Catalogue public" if provider == "numilog-catalog"
                      else "Numilog"),
            "connected": connected}


def _row(*, public=False, store=True, historical=False):
    providers = ["numilog"] if store else []
    sources = ([_source("numilog")] if store else []) + (
        [_source("numilog-catalog")] if public else []
    )
    return {
        "identity_id": "PRIVATE_IDENTITY_2385",
        "providers": providers,
        "sources": sources,
        "statistics": {
            "local_books": 0,
            "known_loans": None,
            "loan_sources_observed": 0,
            "remote_owned_rights": 7 if store else 0,
            "sources": {"numilog-catalog": 3} if historical else {},
        },
        "sync": {},
    }


def test_public_enabled_and_private_store_are_distinct_visible_sources():
    row = _row(public=True, store=True)
    grouped = home.source_groups(row)
    assert [x["provider_id"] for x in grouped["authenticated"]] == ["numilog"]
    assert [x["provider_id"] for x in grouped["public"]] == ["numilog-catalog"]
    public = grouped["public"][0]
    assert public["kind"] == "public"
    assert public["label"] == "Numilog — Catalogue public"
    assert "catalogue public" in home.compact_source_text(public)
    assert "Numilog" in home.compact_source_text(grouped["authenticated"][0])
    assert "7" in home.overview_counts_lines(row["statistics"], row=row)[1]
    assert home._loan_scope_not_configured(row)
    assert not [x for x in home.attention_items(row)
                if x["key"] == "source:numilog-catalog"]


def test_public_singleton_without_any_account_is_not_a_fake_login_or_loan():
    row = _row(public=True, store=False)
    groups = home.compact_source_groups(row)
    assert not groups["authenticated"]
    assert [x["provider_id"] for x in groups["public"]] == ["numilog-catalog"]
    assert home.linked_sources(row) == []
    assert home._loan_scope_not_configured(row)
    items = home.attention_items(row)
    assert not any(x["key"].startswith("source:") for x in items)
    assert not any(x["key"] == "loans-stale" for x in items)
    assert "aucune source de prêt configurée" in home.overview_counts_lines(
        row["statistics"], row=row,
    )[0]
    assert "Droits d’achat distants : 0" in home.overview_counts_lines(
        row["statistics"], row=row,
    )[1]


@pytest.mark.parametrize("store", [False, True])
def test_disabled_catalogue_is_not_reenabled_from_old_artifact_provenance(store):
    row = _row(public=False, store=store, historical=True)
    groups = home.source_groups(row)
    assert not groups["public"]
    assert "numilog-catalog" not in [x["provider_id"] for x in groups["authenticated"]]
    # Files can still keep their original provider provenance, but no account
    # or public catalogue availability can be inferred from those files.
    groups = home.compact_source_groups(row)
    assert "numilog-catalog" not in [x["provider_id"] for x in groups["public"]]
    assert "numilog-catalog" not in [x["provider_id"] for x in groups["authenticated"]]


def test_only_current_public_descriptor_may_enable_public_catalogue_display():
    row = _row(public=True, store=True)
    row["sources"] = [_source("numilog")]
    assert not home.source_groups(row)["public"]
    row["sources"].append(_source("numilog-catalog", connected=False))
    public = home.source_groups(row)["public"]
    assert len(public) == 1 and public[0]["connected"] is False
    # The descriptor was visible, but disconnected is NOT evidence that
    # any catalogue page can currently be fetched. No login prompt either.
    assert home.attention_items(row) == []


def test_unknown_or_private_sources_remain_conservative_for_loan_scope():
    row = _row(public=True, store=False)
    row["sources"].append({"provider_id": "future.library", "connected": True})
    assert not home._loan_scope_not_configured(row)
    assert "loans-stale" in {x["key"] for x in home.attention_items(row)}
    row["sources"] = [{"provider_id": "numilog-catalog", "token": "PRIVATE_TOKEN"}]
    # An unknown extra key must not appear in the public projected text.
    assert "PRIVATE_TOKEN" not in str(home.source_groups(row))
    assert "PRIVATE_TOKEN" not in home.compact_source_text(
        home.source_groups(row)["public"][0],
    )
