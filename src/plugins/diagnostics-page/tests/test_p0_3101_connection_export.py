"""P0 #3101 — Core provider-connect correlation survives Diagnostics export.

Only synthetic public broker observations. No credentials, HTTP, GTK, profiles,
real loan inventory or user logs are accessed.
"""
from __future__ import annotations

import json

import pytest

from diagnostics_explorer import (
    sanitized_rows, linked_operation_groups, export_selected_events,
    published_connection_attempt, published_operation_reference,
)
from diagnostics_selection import (
    collect_filtered_selection, _redacted_page_fingerprint,
)


REF_A = "a" * 24
REF_B = "b" * 24
SECRET = "private.invalid/checkout?auth=NEVER_EXPORT"
WHEN = "2026-10-25T20:00:00+00:00"


def attempt(phase, ref=REF_A, *, source="core_events", index=0):
    state, reason = {
        "requested": ("unknown", "requested"),
        "connect_invoked": ("unknown", "connect_invoked"),
        "session_reused": ("success", "session_reused"),
        "terminal": ("success", "connected"),
    }[phase]
    detail = {
        "provider_id": "bnf-pret",
        "operation_ref": ref,
        "phase": phase, "status": state, "reason_code": reason,
        "caller_category": "startup/connect_configured",
        "source_kind": "configured", "network_auth": "unknown",
        "duration_ms": index,
    }
    return {
        "timestamp": WHEN, "event_id": f"{source}-{ref}-{phase}-{index}",
        "source": source,
        "module": "PLUGIN" if source == "core_events" else "cifereca",
        "provider_id": "bnf-pret",
        "message": f"connexion fournisseur · {phase}",
        "severity": "info", "status": "ok" if state == "success" else "unknown",
        "detail": detail,
    }


def _six():
    return [
        attempt(phase, source=source, index=index)
        for source in ("core_events", "app_log")
        for index, phase in enumerate(("requested", "connect_invoked", "terminal"))
    ]


def _export(rows):
    return json.loads(export_selected_events(rows, {row["event_id"] for row in rows}))


def test_exact_six_phases_cross_two_channels_export_one_request_not_six_connections():
    rows = sanitized_rows({"events": _six()})
    assert len(rows) == 6
    assert {row["operation_ref"] for row in rows} == {REF_A}
    assert {row["network_auth"] for row in rows} == {"unknown"}
    assert not any("task_ref" in row for row in rows)
    groups = linked_operation_groups(rows)
    assert len(groups) == 1
    assert groups[0]["operation_ref"] == REF_A
    assert groups[0]["task_ref"] is None
    assert groups[0]["count"] == 6
    exported = _export(rows)
    assert {x["operation_ref"] for x in exported["events"]} == {REF_A}
    assert {x["phase"] for x in exported["events"]} == {
        "requested", "connect_invoked", "terminal",
    }
    assert {x["caller_category"] for x in exported["events"]} == {
        "startup/connect_configured",
    }
    assert all(x["connection_status"] in {"unknown", "success"}
               for x in exported["events"])
    assert "network_auth" in exported["events"][0]
    assert "task_ref ou operation_ref" in exported["notice"]
    assert "authentifications réseau inconnues" in exported["notice"]
    assert "scope_ref" not in json.dumps(exported)


def test_two_requests_same_timestamp_are_not_fused_with_historical_success():
    events = _six()
    events += [
        attempt("requested", REF_B, index=99),
        attempt("terminal", REF_B, index=100),
        {**attempt("terminal", index=101),
         "event_id": "historical-success", "message": "connexion fournisseur réussie",
         "detail": {"provider_id": "bnf-pret", "code": "provider_connected"}},
    ]
    rows = sanitized_rows({"events": events})
    groups = linked_operation_groups(rows)
    assert len(groups) == 3
    assert sorted(g["count"] for g in groups) == [1, 2, 6]
    assert {g["operation_ref"] for g in groups if g["operation_ref"]} == {REF_A, REF_B}
    historic = next(g for g in groups if g["count"] == 1)
    assert historic["operation_ref"] is None
    assert historic["label"] == "Corrélation non disponible"
    export = _export(rows)
    assert "operation_ref" not in next(x for x in export["events"]
                                       if x["event_id"] == "historical-success")


@pytest.mark.parametrize("mutation", [
    {"operation_ref": REF_B},
    {"detail": {"operation_ref": REF_B}},
    {"module": "PLUGIN"},
    {"source": "app_log"},
    {"provider_id": "another"},
    {"message": "connexion fournisseur réussie"},
    {"status": "error"},
])
def test_broker_envelope_and_top_level_forgery_never_become_operation_reference(mutation):
    good = attempt("terminal")
    row = dict(good, **mutation)
    if mutation.get("module") == "PLUGIN":
        row["source"] = "app_log"
    if mutation.get("source") == "app_log":
        row["module"] = "PLUGIN"
    assert published_connection_attempt(row) is None
    clean = sanitized_rows({"events": [row]})
    assert len(clean) == 1
    assert "operation_ref" not in clean[0]
    assert "operation_ref" not in _export(clean)["events"][0]
    assert linked_operation_groups(clean)[0]["operation_ref"] is None


@pytest.mark.parametrize("mutation", [
    {"operation_ref": SECRET},
    {"phase": "http_login"},
    {"status": "failed"},
    {"reason_code": "private_url_" + SECRET},
    {"caller_category": SECRET},
    {"source_kind": "owned"},
    {"network_auth": "authenticated"},
    {"duration_ms": True},
    {"scope_ref": "abc", "scope_kind": "connection"},
    {"token": SECRET},
])
def test_malformed_private_or_noncanonical_detail_fails_closed(mutation):
    row = attempt("terminal")
    row["detail"] = {**row["detail"], **mutation}
    assert published_connection_attempt(row) is None
    clean = sanitized_rows({"events": [row]})
    assert "operation_ref" not in clean[0]
    assert "operation_ref" not in _export(clean)["events"][0]
    assert SECRET not in _export(clean)["events"][0].get("operation_ref", "")


def test_operation_export_requires_companion_attestation_not_just_hex_ref():
    base = sanitized_rows({"events": [attempt("terminal")]})[0]
    for candidate in (
        {**base, "phase": "fake"},
        {**base, "caller_category": SECRET},
        {**base, "network_auth": "true"},
        {**base, "provider_id": "another"},
        {**base, "operation_ref": SECRET},
        {**base, "message": "unrelated"},
    ):
        assert published_operation_reference(candidate) is None
        exported = _export([candidate])
        assert "operation_ref" not in exported["events"][0]


def test_fingerprint_observes_changed_operation_reference_and_phase():
    before = sanitized_rows({"events": [attempt("requested")]})
    changed = sanitized_rows({"events": [attempt("requested", REF_B)]})
    # An event's ID can remain unchanged when the origin changes in a
    # non-atomic paginated feed; the first-page comparison still rejects it.
    changed[0]["event_id"] = before[0]["event_id"]
    assert _redacted_page_fingerprint(before) != _redacted_page_fingerprint(changed)
    phase_changed = [dict(before[0], phase="connect_invoked")]
    assert _redacted_page_fingerprint(before) != _redacted_page_fingerprint(phase_changed)


def test_full_filtered_selection_multiple_pages_keeps_refs_and_partial_coverage():
    rows = [
        {
            "timestamp": WHEN, "event_id": f"plain-{i}", "source": "app_log",
            "module": "cifereca", "severity": "info", "status": "ok",
            "message": "journal historique non corrélé",
        }
        for i in range(52)
    ] + _six()

    class Broker:
        def __init__(self):
            self.queries = []

        def events(self, **query):
            self.queries.append(dict(query))
            offset, limit = query["offset"], query["limit"]
            page = rows[offset:offset + limit]
            return {
                "events": page, "total": len(rows), "offset": offset,
                "next_offset": offset + len(page) if offset + len(page) < len(rows) else None,
                "sources": [
                    {"source": "core_events", "status": "partial",
                     "reason": "snapshot_records_incomplete"},
                    {"source": "app_log", "status": "available"},
                ],
            }

    broker = Broker()
    selected = collect_filtered_selection(
        broker, source="all", mode="all", period="all",
    )
    exported = json.loads(selected.export_json)
    assert selected.count == 58
    assert exported["selection"]["scope"] == "all_accessible_filtered_pages"
    assert exported["selection"]["sources_partial_or_unavailable"] is True
    correlated = [event for event in exported["events"] if "operation_ref" in event]
    assert len(correlated) == 6
    assert {e["operation_ref"] for e in correlated} == {REF_A}
    assert len(broker.queries) == 3  # two pages + first page re-read
    assert all(query["source"] == "all" for query in broker.queries)
