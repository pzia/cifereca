"""Public broker references only: no time/text/account correlation."""
import json

import pytest

from diagnostics_explorer import (
    linked_operation_groups, sanitized_rows, export_selected_events,
)


def event(n, *, source="core_events", reference=None, message="backup status=partial"):
    row = dict(timestamp="2026-10-20T13:00:00+00:00", source=source,
               module="NUMILOG", severity="warning", event_id=f"{n:016x}",
               message=message, status="warning")
    if reference is not None:
        row["detail"] = {"task_ref": reference}
    return row


def test_real_published_reference_links_channels_preserves_independent_outcomes():
    a = event(1, reference="aaaaaaaaaaaa")
    b = event(2, source="app_log", reference="aaaaaaaaaaaa")
    worker = event(3, source="app_log", message="TRACE task correlation status=success")
    worker.update(task_ref="aaaaaaaaaaaa", severity="info", status="success")
    other = event(4, reference="bbbbbbbbbbbb")
    unrelated = event(5)  # same millisecond/message is not a task reference
    rows = sanitized_rows({"events": [a, b, worker, other, unrelated]}, include_detail=True)
    groups = linked_operation_groups(rows)
    by_ref = {g["task_ref"]: g for g in groups}
    assert len(by_ref["aaaaaaaaaaaa"]["events"]) == 3
    assert by_ref["aaaaaaaaaaaa"]["severity"] == "warning"
    assert {r["status"] for r in by_ref["aaaaaaaaaaaa"]["events"]} == {"warning", "success"}
    assert by_ref["bbbbbbbbbbbb"]["count"] == 1
    assert by_ref[None]["count"] == 1
    assert by_ref[None]["label"] == "Corrélation non disponible"
    assert len(rows) == sum(g["count"] for g in groups)


@pytest.mark.parametrize("reference", [
    "PRIVATE", "abcdef012345\n", "abcdef012345?token=PRIVATE",
    "Ａ" * 12, True, 123, {}, [],
])
def test_malformed_reference_never_labels_or_groups_observations(reference):
    rows = sanitized_rows({"events": [event(1, reference=reference), event(2)]})
    groups = linked_operation_groups(rows)
    assert len(groups) == 2
    assert all(g["task_ref"] is None for g in groups)
    assert all("PRIVATE" not in g["label"] for g in groups)


def test_conflicting_top_and_nested_references_are_not_joined():
    raw = event(1, reference="aaaaaaaaaaaa")
    raw["task_ref"] = "bbbbbbbbbbbb"
    rows = sanitized_rows({"events": [raw]}, include_detail=True)
    assert "task_ref" not in rows[0]
    assert linked_operation_groups(rows)[0]["task_ref"] is None


def test_reference_looking_text_and_event_id_do_not_attest_correlation():
    first = event(1, message="task_ref=aaaaaaaaaaaa")
    first["event_id"] = "aaaaaaaaaaaa"
    second = event(2, message="task_ref=aaaaaaaaaaaa")
    rows = sanitized_rows({"events": [first, second]})
    assert len(linked_operation_groups(rows)) == 2
    assert all("task_ref" not in row for row in rows)


def test_unrecognised_source_never_attests_a_reference():
    rows = sanitized_rows({"events": [event(1, source="plugin", reference="aaaaaaaaaaaa")]})
    assert linked_operation_groups(rows)[0]["task_ref"] is None


def test_linked_and_unlinked_selection_stays_complete_redacted_and_page_bounded():
    raw = [event(n, reference="aaaaaaaaaaaa", message="token=PRIVATE") for n in range(1, 60)]
    rows = sanitized_rows({"events": raw})
    groups = linked_operation_groups(rows)
    assert groups[0]["count"] == 50
    selected = {row["event_id"] for row in rows}
    data = export_selected_events(rows, selected)
    assert "PRIVATE" not in data
    exported = json.loads(data)
    assert len(exported["events"]) == 50
    assert all(row["task_ref"] == "aaaaaaaaaaaa" for row in exported["events"])
