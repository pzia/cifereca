"""#907 — explain per-source youth yield without HTTP or identity leaks."""
from __future__ import annotations

import json

from youth_catalog import (
    YouthCatalogIndex, source_yield_diagnostic, youth_source_support_snapshot,
)
from youth_page import source_diagnostic_presentation


def row(state="empty_unverified", **pipeline):
    return {
        "source_id": "ecole-loisirs", "family": "age",
        "label": "L'École des loisirs",
        "plugin_state": "ready", "enabled": True,
        "collection_state": state, "accepted": 0, "rejected": 0,
        "cache_count": 20,
        "pipeline": pipeline,
    }


def test_only_supported_age_sources_are_included_without_reactivating_retired_plugins():
    snapshot = YouthCatalogIndex().source_diagnostics("reader-identity")
    sources = {x["source_id"]: x for x in snapshot}
    for source in ("milan", "ecole-loisirs"):
        assert sources[source]["family"] == "age"
        assert sources[source]["collection_state"] == "not_tested"
        assert sources[source]["plugin_state"] == "not_installed"
    assert "booktrust" not in sources and "usborne" not in sources
    assert sources["youth.bibliography.openlibrary"]["family"] == "bibliography"


def test_pipeline_indicates_observed_yield_gap_without_proving_remote_zero():
    assert source_yield_diagnostic(row(
        anchor_count=20, candidate_count=0,
    ))["cause"] == "ancres_sans_fiches_reconnues"
    result = source_yield_diagnostic(row(
        anchor_count=20, candidate_count=20, isbn_valid_count=0,
        age_qualified_count=0, query_mode="remote",
    ))
    assert result["cause"] == "candidats_sans_isbn_atteste"
    assert result["confidence"] == "inferred"
    assert result["query_mode"] == "remote"
    assert source_yield_diagnostic(row(
        candidate_count=20, isbn_valid_count=20, age_qualified_count=0,
    ))["cause"] == "candidats_sans_age_atteste"
    assert source_yield_diagnostic(row())["cause"] == "vide_non_verifie"


def test_http_errors_and_success_are_not_mixed_with_undetermined_empty():
    assert source_yield_diagnostic(row("http_error"))["cause"] == "http_echec"
    assert source_yield_diagnostic(row("timeout"))["cause"] == "delai_depasse"
    assert source_yield_diagnostic(row("invalid"))["cause"] == "structure_invalide_ou_rejet"
    success = row("items", candidate_count=20, isbn_valid_count=20)
    success["accepted"] = 20
    assert source_yield_diagnostic(success)["cause"] == "notices_qualifiees"
    assert source_yield_diagnostic(success)["confidence"] == "observed"


def test_support_snapshot_omits_identity_titles_html_signed_links_and_secrets():
    unsafe = row("empty_unverified", anchor_count=20, candidate_count=0)
    unsafe.update({
        "identity_id": "PRIVATE_OWNER", "html": "RAW_HTML_SECRET",
        "title": "secret book title", "url": "https://secret/LOAN/secret",
        "credential_id": "CARD_123",
    })
    unsafe["pipeline"]["private_token"] = "TOKENABC"
    snapshot = youth_source_support_snapshot([unsafe])
    json_text = json.dumps(snapshot)
    for forbidden in (
        "PRIVATE_OWNER", "RAW_HTML_SECRET", "secret book title",
        "/LOAN/secret", "CARD_123", "TOKENABC",
    ):
        assert forbidden not in json_text
    assert snapshot["sources"][0]["counts"]["anchor_count"] == 20
    assert snapshot["sources"][0]["cause"] == "ancres_sans_fiches_reconnues"


def test_diagnostics_rendering_explicitly_labels_hypotheses():
    line = source_diagnostic_presentation((row(
        anchor_count=20, candidate_count=0,
    ),))[1][0]
    assert "ancres sans fiches reconnues" in line
    assert "hypothèse, à vérifier" in line
    assert "cache : 20" in line


def test_unknown_untrusted_youth_provider_id_is_not_exported():
    broken = row()
    broken["source_id"] = "alice@example.com?token=x"
    report = youth_source_support_snapshot([broken])
    assert report["sources"][0]["source_id"] == "unknown"
    assert "alice@" not in json.dumps(report)


def test_gtk_copy_does_not_trigger_collect_or_include_raw_page():
    from pathlib import Path
    s = (Path(__file__).parents[1] / "youth_page.py").read_text()
    assert "def copy_source_diagnostics" in s
    assert "youth_source_support_snapshot(rows)" in s
    assert "display.get_clipboard().set(json.dumps(safe" in s
    assert "source_yield_diagnostic(row)" in s
    assert '"Jeunesse source collection outcome"' in s
