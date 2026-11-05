"""#3252: offline summary never leaks raw journal data or guesses old failures."""
from __future__ import annotations

from cifereca.core.kobo_final_verdict_summary import classify


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
