"""#3252: offline summary never leaks raw journal data or guesses old failures."""
from __future__ import annotations

from scripts.kobo_final_verdict_summary import classify, summarize_document


def _event(*, format="epub", expected="epub", protected="False",
           inspection="True", catalog="True", product="secret"):
    return {
        "message": (
            "TRACE canonical delivery final rejected "
            "code=canonical_delivery_final_invalid provider_id=kobo "
            f"identity_id='private' account_id='private' edition_id='{product}' "
            "right_id='secret' reader=rights_selected_delivery_candidate "
            f"expected_kind={expected} inspection_valid={inspection} "
            f"inspection_format={format} inspection_protected={protected} "
            "inspection_reason=None "
            f"catalog_valid={catalog} catalog_reason=None "
            "bearer=SYNTHETIC_SECRET_DO_NOT_PRINT"
        ),
    }


def test_no_evidence_is_not_success():
    summary = classify([{"message": "other event"}, {"message": "canonical_delivery_final_invalid"}])
    assert summary["status"] == "no_instrumented_evidence"
    assert summary["occurrences"] == 0
    assert summary["instrumented"] == 0


def test_real_closed_fields_classify_multiple_vetoes_without_item_identifiers():
    events = [
        _event(protected="True", product="secret-1"),
        _event(format="pdf", product="secret-2"),
        _event(inspection="False", catalog="False", product="secret-3"),
    ]
    summary = classify(events)
    assert summary["status"] == "instrumented_occurrences"
    assert summary["occurrences"] == summary["instrumented"] == 3
    assert summary["reasons"] == {
        "catalog_invalid": 1,
        "format_mismatch": 1,
        "inspection_invalid": 1,
        "multiple_failed_predicates": 1,
        "protected": 1,
    }
    text = str(summary)
    assert "secret" not in text and "private" not in text
    assert "bearer" not in text


def test_partial_trace_is_not_classified_or_assumed_repaired():
    report = classify([
        _event(protected="True"),
        {"message": (
            "TRACE canonical delivery final rejected "
            "code=canonical_delivery_final_invalid provider_id=kobo "
            "inspection_valid=True"
        )},
        _event(protected="OTHER"),
    ])
    assert report["status"] == "partial_verdicts"
    assert report["occurrences"] == 3
    assert report["instrumented"] == 1
    assert report["reasons"]["verdict_incomplete"] == 2


def test_non_kobo_and_nonfinal_errors_ignored():
    event = _event()
    event["message"] = event["message"].replace("provider_id=kobo ", "provider_id=numilog ")
    assert classify([event])["occurrences"] == 0
    assert classify([None, "random"])["status"] == "no_instrumented_evidence"


def test_missing_evidence_with_gapped_rotations_is_not_clean():
    report = summarize_document({"events": []}, manifest_coverage={
        "coverage": "partial", "rotation_gaps_present": True,
        "active_log_missing": False,
    })
    assert report["status"] == "no_evidence_partial_coverage"
    assert report["journal_coverage"] == "partial"
    assert report["historical_completeness_attested"] is False


def test_available_retained_window_does_not_prove_historical_completeness():
    report = summarize_document({"events": []}, manifest_coverage={
        "coverage": "available", "rotation_gaps_present": False,
        "active_log_missing": False,
    })
    assert report["status"] == "no_instrumented_evidence"
    assert report["journal_coverage"] == "available"
    assert report["historical_completeness_attested"] is False


def test_raw_journal_must_not_self_attest_coverage_even_if_forged():
    report = summarize_document({
        "events": [], "coverage": "available",
        "rotation_gaps": [], "active_log_missing": False,
    })
    assert report["status"] == "no_instrumented_evidence"
    assert report["journal_coverage"] == "unknown"
    assert report["historical_completeness_attested"] is False


def test_unattested_manifest_metadata_is_not_treated_as_complete():
    report = summarize_document({"events": []}, manifest_coverage={
        "coverage": "available", "rotation_gaps_present": None,
        "active_log_missing": False,
    })
    assert report["journal_coverage"] == "unknown"
    report = summarize_document({"events": []}, manifest_coverage={
        "coverage": "available", "rotation_gaps_present": False,
        "active_log_missing": True,
    })
    assert report["journal_coverage"] == "partial"


def test_invalid_document_is_never_a_success():
    result = summarize_document([])
    assert result["status"] == "invalid_journal"
    assert result["journal_coverage"] == "unknown"
    assert result["historical_completeness_attested"] is False


def test_closed_encryption_profile_counts_never_echo_unknown_private_markers():
    aes = _event(protected="True")
    aes["message"] += " encryption_profile=aes_algorithm"
    fonts = _event(protected="True")
    fonts["message"] += " encryption_profile=font_obfuscation_algorithms_only"
    private = _event(protected="True")
    private["message"] += " encryption_profile=PRIVATE_KEY_MUST_NOT_LEAK"
    report = classify([aes, fonts, private])
    assert report["occurrences"] == 3
    assert report["reasons"]["protected"] == 3
    assert report["encryption_profiles"] == {
        "aes_algorithm": 1,
        "font_obfuscation_algorithms_only": 1,
    }
    assert "PRIVATE_KEY" not in str(report)


def test_route_profile_matrix_separates_two_runtime_and_direct_failures():
    events = []
    for route, guard, marker in (
        ("downloadable_selected_delivery_candidate_runtime_guarded",
         "True", "aes_algorithm"),
        ("downloadable_selected_delivery_candidate_runtime_guarded",
         "True", "adobe_adept_marker"),
        ("downloadable_selected_delivery_candidate_guarded",
         "False", "font_obfuscation_algorithms_only"),
    ):
        event = _event(protected="True", product="PRIVATE_EXACT_EDITION")
        event["message"] = event["message"].replace(
            "reader=rights_selected_delivery_candidate",
            f"reader={route} runtime_guarded={guard}",
        )
        event["message"] += f" encryption_profile={marker}"
        events.append(event)
    result = classify(events)
    assert result["route_encryption_profiles"] == {
        "direct:font_obfuscation_algorithms_only": 1,
        "runtime:adobe_adept_marker": 1,
        "runtime:aes_algorithm": 1,
    }
    assert result["route_unqualified_occurrences"] == 0
    assert result["occurrences"] == 3
    assert "PRIVATE_EXACT_EDITION" not in str(result)


def test_route_mismatch_never_associates_runtime_or_direct_speculatively():
    event = _event(protected="True")
    event["message"] = event["message"].replace(
        "reader=rights_selected_delivery_candidate",
        "reader=downloadable_selected_delivery_candidate_runtime_guarded "
        "runtime_guarded=False",
    )
    event["message"] += " encryption_profile=aes_algorithm"
    result = classify([event])
    assert result["encryption_profiles"] == {"aes_algorithm": 1}
    assert result["route_encryption_profiles"] == {}
    assert result["route_unqualified_occurrences"] == 1


def test_route_counts_are_occurrences_not_distinct_editions():
    event = _event(protected="True")
    event["message"] = event["message"].replace(
        "reader=rights_selected_delivery_candidate",
        "reader=downloadable_selected_delivery_candidate_guarded "
        "runtime_guarded=False",
    )
    event["message"] += " encryption_profile=aes_algorithm"
    report = classify([event, event, event])
    assert report["occurrences"] == 3
    assert report["route_encryption_profiles"] == {"direct:aes_algorithm": 3}
    assert "distinct" in report["note"]
