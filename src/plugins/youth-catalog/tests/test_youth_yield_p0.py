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
    assert result["cause"] == "candidats_sans_age_atteste"
    assert result["confidence"] == "inferred"
    assert result["query_mode"] == "remote"
    assert source_yield_diagnostic(row(
        candidate_count=20, isbn_valid_count=20, age_qualified_count=0,
    ))["cause"] == "candidats_sans_age_atteste"
    assert source_yield_diagnostic(row())["cause"] == "vide_non_verifie"


def test_optional_isbn_does_not_explain_missing_notices_without_observed_rejections():
    item = row(
        anchor_count=20, candidate_count=20, isbn_valid_count=0,
        age_qualified_count=20, query_mode="remote",
    )
    assert source_yield_diagnostic(item)["cause"] == "perte_apres_qualification_a_examiner"
    item["pipeline"]["rejection_reasons"] = {"isbn_invalid": 17}
    assert source_yield_diagnostic(item)["cause"] == "candidats_avec_isbn_rejete"


def test_inventory_ids_and_labels_cannot_expose_untrusted_private_source_names():
    catalog = YouthCatalogIndex()
    inventory = (
        {"plugin_id": "youth.source.milan", "type": "youth.source",
         "state": "ready", "enabled": True,
         "package_id": "https://account.example/path?token=SECRET"},
        {"plugin_id": "youth.source.reader@example.com?token=PRIVATE",
         "type": "youth.source", "state": "ready", "enabled": True},
        {"plugin_id": "youth.source.custom-source", "type": "youth.source",
         "state": "ready", "enabled": True},
    )
    rows = catalog.source_diagnostics("identity-PRIVATE", inventory)
    ids = {item["source_id"] for item in rows}
    assert "milan" in ids and "custom-source" in ids
    assert "reader@example.com?token=PRIVATE" not in ids
    assert next(x for x in rows if x["source_id"] == "milan")["package_id"] == ""
    display = source_diagnostic_presentation((
        {"source_id": "reader@example.com?token=PRIVATE",
         "label": "Alice token=PRIVATE", "family": "age"},
        {"source_id": "custom-source", "label": "Alice token=PRIVATE",
         "family": "age"},
    ))
    message = " ".join(display[1])
    assert "PRIVATE" not in message and "Alice" not in message
    assert "Source inconnue" in message and "custom-source" in message


def test_isbnless_catalogue_funnel_survives_identity_cache_diagnostics_and_safe_export():
    index = YouthCatalogIndex()
    source_funnel = {
        "query_mode": "remote", "http_status": 200,
        "cards_detected": 20, "cards_complete": 20,
        "candidate_count": 20, "analyzed_count": 20,
        "age_qualified_count": 20, "isbn_valid_count": 0,
        "missing_isbn": 20, "covers_seen": 20,
        "cover_urls_valid": 18, "author_count": 29,
        "returned_items": 20,
        # Deliberately unsafe provider data must never reach UI/support.
        "html": "<script>PRIVATE_HTML</script>",
        "url": "https://private.example/?token=PRIVATE_URL",
        "title": "PRIVATE_TITLE",
        "private_token": "PRIVATE_TOKEN",
    }
    index.record_collection(
        "identity-A-SECRET", "ecole-loisirs", state="items",
        accepted=20, received_count=20, request_count=1,
        pipeline=source_funnel,
    )
    rows_a = index.source_diagnostics("identity-A-SECRET")
    rows_b = index.source_diagnostics("identity-B-SECRET")
    ecole_a = next(item for item in rows_a if item["source_id"] == "ecole-loisirs")
    ecole_b = next(item for item in rows_b if item["source_id"] == "ecole-loisirs")
    assert ecole_a["collection_state"] == "items"
    assert ecole_a["request_count"] == 1
    assert ecole_b["collection_state"] == "not_tested"
    assert ecole_b["pipeline"] == {}
    assert ecole_a["pipeline"]["missing_isbn"] == 20
    assert ecole_a["pipeline"]["cards_complete"] == 20
    assert source_yield_diagnostic(ecole_a)["cause"] == "notices_qualifiees"
    assert source_yield_diagnostic(ecole_a)["confidence"] == "observed"
    line = " ".join(source_diagnostic_presentation(rows_a)[1])
    assert "notices sans ISBN publié : 20" in line
    assert "cartes catalogue : 20" in line
    assert "couvertures publiques valides : 18" in line
    export = youth_source_support_snapshot(rows_a)
    safe = json.dumps(export, ensure_ascii=False)
    ecole_export = next(item for item in export["sources"] if item["source_id"] == "ecole-loisirs")
    for key, value in (("missing_isbn", 20), ("cards_detected", 20),
                       ("cards_complete", 20), ("cover_urls_valid", 18)):
        assert ecole_export["counts"][key] == value
    for secret in ("PRIVATE_HTML", "PRIVATE_URL", "PRIVATE_TITLE",
                   "PRIVATE_TOKEN", "identity-A-SECRET", "identity-B-SECRET"):
        assert secret not in safe
        assert secret not in line


def test_card_metrics_are_strictly_bounded_before_support_and_ui():
    malicious = row("items",
                    cards_detected=True, cards_complete=-1,
                    missing_isbn=100_001, covers_seen="PRIVATE_TOKEN",
                    cover_urls_valid=2, author_count=100_000,
                    private_token="PRIVATE_TOKEN")
    malicious["accepted"] = 2
    counts = source_yield_diagnostic(malicious)["counts"]
    assert "cards_detected" not in counts
    assert "cards_complete" not in counts
    assert "missing_isbn" not in counts
    assert "covers_seen" not in counts
    assert counts["cover_urls_valid"] == 2
    assert counts["author_count"] == 100_000
    payload = json.dumps(youth_source_support_snapshot((malicious,)))
    assert "PRIVATE_TOKEN" not in payload


def test_secondary_thumbnails_survive_only_as_safe_per_identity_counts():
    index = YouthCatalogIndex()
    index.record_collection(
        "camille-PRIVATE", "ecole-loisirs", state="items", accepted=2,
        pipeline={
            "cards_detected": 2, "cards_complete": 2,
            "thumbnail_card_count": 20,
            "thumbnail_duplicate_count": 2,
            "rejection_reasons": {"duplicate": 1, "age_unattested": 1},
            "thumbnail_urls": ["https://private.example/?token=SECRET"],
        },
    )
    camille = {x["source_id"]: x for x in index.source_diagnostics("camille-PRIVATE")}
    alex = {x["source_id"]: x for x in index.source_diagnostics("alex-PRIVATE")}
    p = camille["ecole-loisirs"]["pipeline"]
    assert p["thumbnail_card_count"] == 20
    assert p["thumbnail_duplicate_count"] == 2
    assert p["rejection_reasons"] == {"duplicate": 1, "age_unattested": 1}
    assert alex["ecole-loisirs"]["collection_state"] == "not_tested"
    assert alex["ecole-loisirs"]["pipeline"] == {}
    summary, lines = source_diagnostic_presentation((camille["ecole-loisirs"],))
    assert "vignettes secondaires : 20" in lines[0]
    assert "vignettes d’éditions déjà présentes : 2" in lines[0]
    report = youth_source_support_snapshot((camille["ecole-loisirs"],))
    counts = report["sources"][0]["counts"]
    assert counts["thumbnail_card_count"] == 20
    assert counts["thumbnail_duplicate_count"] == 2
    encoded = json.dumps(report, ensure_ascii=False) + " ".join(lines)
    assert "SECRET" not in encoded and "PRIVATE" not in encoded

    source = {
        "thumbnail_card_count": "20", "thumbnail_duplicate_count": True,
        "cards_detected": 2, "cards_complete": 2,
    }
    assert "thumbnail_card_count" not in source_yield_diagnostic(row("items", **source))["counts"]
    assert "thumbnail_duplicate_count" not in source_yield_diagnostic(row("items", **source))["counts"]


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
