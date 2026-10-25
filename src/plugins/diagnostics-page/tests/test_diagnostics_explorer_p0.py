"""P0 Diagnostics explorer: public broker only, paginated and offline."""
from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from diagnostics_explorer import (
    PAGE_SIZE, SOURCES, SEVERITIES, PERIODS,
    explorer_since, fetch_explorer_page, sanitized_rows, module_groups,
    source_summaries, diagnostic_channel_caption, explorer_header,
    export_selected_events, next_page_offset,
    selectable_event_ids,
)


class Broker:
    def __init__(self, payload=None):
        self.calls = []
        self.payload = payload or {
            "events": [], "sources": [
                {"source": "core_events", "status": "empty"},
                {"source": "app_log", "status": "unavailable"},
            ],
            "total": 0, "offset": 0, "next_offset": None,
        }

    def events(self, **filters):
        self.calls.append(filters)
        return self.payload


def event(event_id, *, module="cifereca.sync", source="app_log",
          severity="info", message="TRACE sync source status=ok"):
    return {
        "timestamp": "2026-10-11T07:00:00+02:00", "event_id": event_id,
        "module": module, "source": source, "severity": severity, "message": message,
    }


def test_utc_periods_are_timezone_aware_and_all_is_unbounded():
    now = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
    assert explorer_since("all", now=now) is None
    assert explorer_since("hour", now=now) == "2026-10-11T11:00:00+00:00"
    assert explorer_since("day", now=now) == "2026-10-10T12:00:00+00:00"
    assert explorer_since("week", now=now) == "2026-10-04T12:00:00+00:00"
    assert explorer_since("month", now=now) == "2026-09-11T12:00:00+00:00"
    with pytest.raises(ValueError):
        explorer_since("unknown", now=now)


def test_only_public_broker_called_with_exact_bounded_filters():
    broker = Broker()
    response = fetch_explorer_page(
        broker, source="app_log", module="cifereca.sync",
        severity="warning", period="day", offset=100,
    )
    assert response == broker.payload
    assert len(broker.calls) == 1
    args = broker.calls[0]
    assert args["source"] == "app_log"
    assert args["module"] == "cifereca.sync"
    assert args["severity"] == "warning"
    assert args["limit"] == PAGE_SIZE == 50
    assert args["offset"] == 100
    assert args["until"] is None
    assert args["since"].endswith("+00:00")
    assert "path" not in args
    assert "credentials" not in args


@pytest.mark.parametrize("filters", [
    {"source": "local_file"}, {"severity": "security"},
    {"offset": -1}, {"offset": 1000001}, {"offset": True},
    {"period": "forever"},
])
def test_invalid_filter_fails_before_broker_access(filters):
    broker = Broker()
    with pytest.raises(ValueError):
        fetch_explorer_page(broker, **filters)
    assert broker.calls == []


def test_search_and_sort_are_current_page_only_and_stable():
    payload = {"events": [
        event("a", message="acquisition ok", severity="info"),
        event("b", message="return failure", severity="error"),
        event("c", message="return confirmed", severity="info"),
    ]}
    newest = sanitized_rows(payload, search="return")
    assert [row["event_id"] for row in newest] == ["c", "b"]
    oldest = sanitized_rows(payload, search="return", newest_first=False)
    assert [row["event_id"] for row in oldest] == ["b", "c"]
    assert "secret" not in oldest[0]
    assert len(sanitized_rows({"events": [event(str(x)) for x in range(90)]})) == 50


def test_module_hierarchy_groups_source_and_severity_and_counts():
    rows = [
        event("1", module="cifereca.sync", severity="info"),
        event("2", module="cifereca.sync", severity="error"),
        event("3", module="cifereca.sync", source="core_events", severity="warning"),
        event("4", module="cifereca.adept", severity="warning"),
    ]
    groups = module_groups(rows)
    assert [(g["module"], g["source"], g["count"]) for g in groups] == [
        ("cifereca.adept", "app_log", 1),
        ("cifereca.sync", "app_log", 2),
        ("cifereca.sync", "core_events", 1),
    ]
    assert groups[1]["severity"] == "error"
    assert groups[2]["severity"] == "warning"


def test_availability_states_are_explicit_even_when_sources_have_no_events():
    states = source_summaries({
        "sources": [
            {"source": "app_log", "status": "partial"},
            {"source": "core_events", "status": "disabled"},
        ],
        "events": [], "offset": 0, "total": 0,
    })
    assert [s["label"] for s in states] == [
        "Journal partiel (rotation / troncation)", "Désactivée",
    ]
    assert explorer_header({"sources": [], "total": 0}, []) == (
        "Canaux de diagnostic indisponibles — aucune donnée exploitable"
    )
    assert "recherche dans cette page uniquement" in explorer_header(
        {"sources": [{"source": "app_log"}], "total": 40, "offset": 50},
        [event("1")], search="return",
    )


def test_export_only_selected_event_ids_pseudonyms_and_public_fields():
    rows = [
        event("first", message="TRACE sync ok"),
        event("second", message="Bearer ABC123 alice@example.com /home/alex"),
    ]
    rows[1]["runtime_stdin"] = "SHOULD_NOT_LEAK"
    exported = export_selected_events(rows, {"second"})
    payload = json.loads(exported)
    assert len(payload["events"]) == 1
    assert payload["events"][0]["event_id"] == "second"
    for preserved in ("ABC123", "alice@example.com", "/home/alex"):
        assert preserved in exported
    assert "SHOULD_NOT_LEAK" not in exported  # unrecognized broker field remains excluded
    assert "corrélation" in payload["notice"]


def test_no_task_correlation_is_fabricated_from_unrelated_event_ids():
    payload = {"events": [
        event("a", module="cifereca.sync"),
        event("b", module="cifereca.sync"),
    ]}
    rows = sanitized_rows(payload)
    groups = module_groups(rows)
    assert len(groups) == 1
    assert len(groups[0]["events"]) == 2
    assert all("correlation_id" not in row for row in groups[0]["events"])


def test_sort_uses_utc_instant_not_offset_lexicography():
    earlier = event("earlier")
    later = event("later")
    earlier["timestamp"] = "2026-10-11T10:00:00+02:00"  # 08:00Z
    later["timestamp"] = "2026-10-11T09:30:00+00:00"    # 09:30Z
    payload = {"events": [earlier, later]}
    assert [r["event_id"] for r in sanitized_rows(payload)] == ["later", "earlier"]
    assert [r["event_id"] for r in sanitized_rows(payload, newest_first=False)] == ["earlier", "later"]


def test_invalid_or_unzoned_timestamps_sort_last_without_crashing():
    rows = [event("bad"), event("naive"), event("valid")]
    rows[0]["timestamp"] = "invalid"
    rows[1]["timestamp"] = "2026-10-11T07:00:00"
    assert [r["event_id"] for r in sanitized_rows({"events": rows})][0] == "valid"
    assert {r["event_id"] for r in sanitized_rows({"events": rows})} == {"bad", "naive", "valid"}


def test_malformed_event_collection_is_ignored_without_iterating_mapping_keys():
    for bad in ("untrusted", {"message": "SECRET"}, 12, object()):
        assert sanitized_rows({"events": bad}) == []


def test_malformed_event_values_are_not_stringified_or_exposed():
    class Secret:
        def __str__(self):
            raise AssertionError("unexpected object stringification")
    rows = sanitized_rows({"events": [{"timestamp": Secret(), "message": Secret(),
                                       "event_id": "safe", "module": 12}]})
    assert len(rows) == 1
    assert rows[0]["message"] == ""
    assert rows[0]["timestamp"] == ""
    assert rows[0]["module"] == ""


def test_source_summary_ignores_malformed_source_containers_and_values():
    for bad in ("unexpected", {"source": "secret"}, 12, object()):
        assert source_summaries({"sources": bad}) == []
    class Secret:
        def __str__(self):
            raise AssertionError("unexpected object stringification")
    assert source_summaries({"sources": [{"source": Secret(), "status": Secret()}]}) == [
        {"source": "inconnue", "status": "unavailable", "label": "Indisponible"},
    ]


@pytest.mark.parametrize("module", [None, 123, [], "x" * 91])
def test_invalid_module_rejected_before_broker_call(module):
    broker = Broker()
    with pytest.raises(ValueError):
        fetch_explorer_page(broker, module=module)
    assert broker.calls == []


@pytest.mark.parametrize("total,offset", [
    ("unknown", "none"), (None, None), ([], {}), (float("inf"), float("nan")),
])
def test_malformed_header_counts_do_not_crash(total, offset):
    header = explorer_header({"sources": [{"source": "app_log"}],
                              "total": total, "offset": offset}, [])
    assert "0 événements affichés" in header
    assert "page à partir de" in header

@pytest.mark.parametrize("next_cursor", [None, -1, 0, 51, 10001, "50", True, 1.5])
def test_next_page_rejects_invalid_or_nonmonotonic_cursors(next_cursor):
    assert next_page_offset({"offset": 0, "next_offset": next_cursor}, current_offset=0) is None


def test_next_page_validates_current_page_and_bounds():
    assert next_page_offset({"offset": 0, "next_offset": 50, "total": 100}, current_offset=0) == 50
    assert next_page_offset({"offset": 9950, "next_offset": 10000, "total": 10001}, current_offset=9950) == 10000
    assert next_page_offset({"offset": 0, "next_offset": 50}, current_offset=50) is None
    assert next_page_offset({"offset": "0", "next_offset": 50}, current_offset=0) is None
    assert next_page_offset({"offset": 0, "next_offset": 50}, current_offset=True) is None


def test_export_keeps_all_fifty_selected_events_without_node_budget_loss():
    rows = [event(f"id-{i}") for i in range(PAGE_SIZE)]
    result = json.loads(export_selected_events(rows, {r["event_id"] for r in rows}))
    assert len(result["events"]) == PAGE_SIZE
    assert {r["event_id"] for r in result["events"]} == {r["event_id"] for r in rows}
    assert all(set(r) == {
        "timestamp", "source", "module", "severity", "message", "status", "event_id"
    } for r in result["events"])


def test_oversize_export_fails_instead_of_silently_returning_preview():
    rows = [event(f"id-{i}", message="x" * 500) for i in range(PAGE_SIZE)]
    with pytest.raises(ValueError, match="24 KB"):
        export_selected_events(rows, {r["event_id"] for r in rows})


def test_export_rejects_more_than_one_page_selection():
    rows = [event(f"id-{i}") for i in range(PAGE_SIZE + 1)]
    with pytest.raises(ValueError, match="too many"):
        export_selected_events(rows, {r["event_id"] for r in rows})


def test_page_controls_use_guarded_cursor_and_handle_export_errors():
    from pathlib import Path
    page = Path(__file__).resolve().parents[1] / "cifereca_diagnostics_page.py"
    code = page.read_text(encoding="utf-8")
    assert "next_page_offset(current_page, current_offset=page_offset)" in code
    assert "except ValueError:" in code
    assert "Export trop volumineux" in code

def test_failed_refresh_clears_stale_events_before_showing_error():
    from pathlib import Path
    page = Path(__file__).resolve().parents[1] / "cifereca_diagnostics_page.py"
    code = page.read_text(encoding="utf-8")
    start = code.index("        def load_events(")
    end = code.index("        def reset_events(", start)
    refresh = code[start:end]
    failure = refresh[refresh.index("            except Exception as exc:"):]
    assert 'current_page = {' in failure
    assert '"events": [], "sources": []' in failure
    assert "display_events()" in failure
    assert failure.index("display_events()") < failure.index("Source indisponible")


def test_refresh_invalidates_previous_page_before_async_request():
    from pathlib import Path
    ui = (Path(__file__).resolve().parents[1] /
          "cifereca_diagnostics_page.py").read_text(encoding="utf-8")
    refresh = ui.split("        def load_events(", 1)[1].split("        def reset_events(", 1)[0]
    clear = refresh.index('                invalidate_selection("Sélection effacée après actualisation ou navigation")')
    blank = refresh.index('            current_page = {', clear)
    rerender = refresh.index('            display_events()', blank)
    submit = refresh.index('                handle = self.tasks.submit(', rerender)
    assert clear < blank < rerender < submit


def test_header_ignores_objects_with_custom_integer_conversion():
    class Untrusted:
        def __int__(self):
            raise AssertionError("must not call custom __int__")
    result = explorer_header({
        "sources": [{"source": "app_log"}],
        "total": Untrusted(), "offset": Untrusted(),
    }, [])
    assert "0 disponibles" in result
    assert "page à partir de 1" in result


def test_export_does_not_stringify_untrusted_broker_values():
    class Untrusted:
        def __str__(self):
            raise AssertionError("never stringify broker objects")

    rows = [{"event_id": "safe-id", "message": Untrusted(),
             "timestamp": "2026-10-11T07:00:00+02:00", "source": "app_log"}]
    exported = json.loads(export_selected_events(rows, {"safe-id"}))
    assert len(exported["events"]) == 1
    assert exported["events"][0]["message"] == ""
    assert exported["events"][0]["event_id"] == "safe-id"


@pytest.mark.parametrize("filters", [
    {"source": []}, {"source": {}}, {"severity": []}, {"severity": {}},
    {"period": []}, {"period": {}}, {"source": None}, {"severity": 1},
])
def test_malformed_selector_types_fail_without_broker_access(filters):
    broker = Broker()
    with pytest.raises(ValueError):
        fetch_explorer_page(broker, **filters)
    assert broker.calls == []


@pytest.mark.parametrize("search", [[], {}, 123, object()])
def test_invalid_local_search_rejected_cleanly(search):
    with pytest.raises(ValueError, match="search"):
        sanitized_rows({"events": [event("safe")]}, search=search)


@pytest.mark.parametrize("sort", [None, 1, [], "descending"])
def test_invalid_sort_direction_rejected_cleanly(sort):
    with pytest.raises(ValueError, match="sort"):
        sanitized_rows({"events": [event("safe")]}, newest_first=sort)


@pytest.mark.parametrize("payload", [None, [], "unexpected", 123, object()])
def test_header_refuses_non_mapping_broker_response(payload):
    assert explorer_header(payload, []) == (
        "Canaux de diagnostic indisponibles — aucune donnée exploitable"
    )


def test_export_rejects_invalid_unicode_without_crashing_ui():
    rows = [event("invalid", message="bad-\ud800-surrogate")]
    with pytest.raises(ValueError, match="invalid Unicode"):
        export_selected_events(rows, {"invalid"})


def test_export_preserves_non_credential_fields_near_500_character_cutoff():
    text = "x" * 480 + " token=SUPERPRIVATESECRET-" + "z" * 40
    row = event("bounded", message=text)
    payload = json.loads(export_selected_events([row], {"bounded"}))
    message = payload["events"][0]["message"]
    assert "SUPERPRIVATE" in message
    assert "token=SUPERPRIVATE" in message
    assert len(message) <= 500


def test_normalizer_bounds_long_non_credential_fields_without_global_redaction():
    # Long messages remain bounded; non-credential tokens are not masked.
    text = "x" * 490 + " token=SUPERPRIVATESECRET-" + "z" * 40
    rows = sanitized_rows({"events": [event("bounded", message=text)]})
    assert len(rows) == 1
    shown = rows[0]["message"]
    assert "token=SUP" in shown
    assert len(shown) <= 500


@pytest.mark.parametrize("now", [0, False, "", {}, [], object()])
def test_period_refuses_invalid_clock_values(now):
    with pytest.raises(ValueError, match="reference time"):
        explorer_since("day", now=now)


def test_module_groups_fail_closed_on_malformed_fields():
    rows = [None, {}, {"module": [], "source": {}, "severity": []},
            {"module": "valid", "source": "app_log", "severity": "error"}]
    groups = module_groups(rows)
    assert len(groups) == 2
    assert next(group for group in groups if group["module"] == "valid")["severity"] == "error"
    assert next(group for group in groups if group["module"] == "Inconnu")["severity"] == "unknown"


def test_module_groups_are_page_bounded():
    rows = [event(str(i), module="m" + str(i)) for i in range(PAGE_SIZE + 10)]
    assert sum(group["count"] for group in module_groups(rows)) == PAGE_SIZE


def test_search_rejects_excessive_input():
    with pytest.raises(ValueError, match="search"):
        sanitized_rows({"events": [event("x")]}, search="x" * 501)


def test_cursor_respects_broker_total():
    assert next_page_offset({"offset": 0, "next_offset": 50, "total": 50}, current_offset=0) is None
    assert next_page_offset({"offset": 0, "next_offset": 50, "total": 51}, current_offset=0) == 50


def test_selection_requires_unique_nonempty_event_ids():
    rows = [event(""), event("collision"), event("collision"), event("unique")]
    assert selectable_event_ids(rows) == {"unique"}
    for invalid in ("", "collision"):
        with pytest.raises(ValueError, match="ambiguous"):
            export_selected_events(rows, {invalid})
    payload = json.loads(export_selected_events(rows, {"unique"}))
    assert [row["event_id"] for row in payload["events"]] == ["unique"]


def test_selection_disabled_for_ambiguous_references_in_ui():
    from pathlib import Path
    page = (Path(__file__).resolve().parents[1] /
            "cifereca_diagnostics_page.py").read_text(encoding="utf-8")
    assert "select.set_sensitive(event_id in valid_ids)" in page
    assert "visible_ids = selectable_event_ids(rows)" in page


@pytest.mark.parametrize("mode", ["readable", "json", "raw"])
def test_diagnostic_detail_respects_24k_utf8_byte_budget(mode):
    from cifereca_diagnostics_page import format_diagnostic_detail
    data = {f"field_{i:02d}": "é" * 400 for i in range(48)}
    text = format_diagnostic_detail(data, mode)
    assert len(text.encode("utf-8")) <= 24_000
    if mode == "json":
        assert json.loads(text)["truncated"] is True


@pytest.mark.parametrize("mode", ["readable", "json", "raw"])
def test_diagnostic_detail_handles_invalid_unicode(mode):
    from cifereca_diagnostics_page import format_diagnostic_detail
    result = format_diagnostic_detail({"message": "bad-" + chr(0xD800)}, mode)
    assert len(result.encode("utf-8")) <= 24_000
    assert "bad-" not in result
    if mode == "json":
        assert json.loads(result)["truncated"] is True


def test_source_summaries_only_publish_attested_core_reason_codes():
    payload = {
        "sources": [{
            "source": "alice@example.com", "status": "partial",
            "reason": "journal rotaté : token=PRIVATE_TOKEN Alice Dupont",
        }, {
            "source": "app_log", "status": "unavailable",
            "reason": "/home/alice/private/cifereca.log",
        }, {
            "source": "core_events", "status": "disabled",
            "reason": "diagnostics_disabled",
        }, {
            "source": "app_log", "status": "partial",
            "reason": "log_tail_bounded",
        }],
    }
    states = source_summaries(payload)
    assert states == [
        {"source": "inconnue", "status": "partial",
         "label": "Journal partiel (rotation / troncation)"},
        {"source": "app_log", "status": "unavailable", "label": "Indisponible"},
        {"source": "core_events", "status": "disabled", "label": "Désactivée",
         "reason": "diagnostics_disabled"},
        {"source": "app_log", "status": "partial",
         "label": "Journal partiel (rotation / troncation)",
         "reason": "log_tail_bounded"},
    ]
    for private in ("PRIVATE_TOKEN", "Alice Dupont", "/home/alice",
                    "alice@example.com", "journal rotaté"):
        assert private not in repr(states)


@pytest.mark.parametrize("source,reason,expected", [
    ("app_log", "log_tail_bounded", "log_tail_bounded"),
    ("app_log", "no_matching_events", "no_matching_events"),
    ("core_events", "diagnostics_disabled", "diagnostics_disabled"),
    ("core_events", "snapshot_unavailable", "snapshot_unavailable"),
    ("core_events", "no_recorded_events", "no_recorded_events"),
    ("core_events", "log_tail_bounded", ""),
    ("app_log", "snapshot_unavailable", ""),
    ("app_log", "Liseuse de Camille", ""),
])
def test_source_reason_never_crosses_publisher_or_unknown_code_boundary(source, reason, expected):
    states = source_summaries({"sources": [{
        "source": source, "status": "partial", "reason": reason,
    }]})
    assert states[0].get("reason", "") == expected
    assert reason == expected or reason not in repr(states)


def test_source_summaries_never_stringify_malformed_reason_or_long_status():
    class Secret:
        def __str__(self):
            raise AssertionError("must not stringify")

    states = source_summaries({"sources": [
        {"source": Secret(), "status": "unknown" * 10000, "reason": Secret()},
        {"source": "app_log", "status": "available", "reason": "PRIVATE_SHOULD_NOT_SHOW"},
    ]})
    assert states[0] == {
        "source": "inconnue", "status": "unavailable", "label": "Indisponible",
    }
    assert states[1] == {
        "source": "app_log", "status": "available", "label": "Alimentée",
    }


def test_source_summaries_do_not_expose_partial_tokens_cut_at_display_bound():
    source = "prefix-" + "x" * 35 + " token=PRIVATE_SUFFIX"
    states = source_summaries({"sources": [
        {"source": source, "status": "disabled",
         "reason": "Error\nAuthorization: Bearer PRIVATE_HEADER"},
    ]})
    shown = repr(states)
    assert "PRIVATE_SUFFIX" not in shown
    assert "PRIVATE_HEADER" not in shown
    assert states[0]["source"] == "inconnue"
    assert "reason" not in states[0]


def test_journals_ui_shows_optional_expurgated_source_reasons():
    from pathlib import Path
    page = (Path(__file__).parents[1] /
            "cifereca_diagnostics_page.py").read_text(encoding="utf-8")
    helper = (Path(__file__).parents[1] /
              "diagnostics_explorer.py").read_text(encoding="utf-8")
    assert 'diagnostic_channel_caption(current_page)' in page
    assert 'states = source_summaries(payload)' in helper
    assert 'reason = state.get("reason")' in helper
    assert '_CHANNEL_REASON_LABELS.get(reason,' in helper


def test_source_summaries_normalize_only_valid_last_event_dates():
    states = source_summaries({"sources": [
        {"source": "core_events", "status": "available",
         "last_event": "2026-10-11T14:00:00+02:00"},
        {"source": "app_log", "status": "partial",
         "last_event": "2026-10-11T11:30:00Z"},
    ]})
    assert states[0]["last_event"] == "2026-10-11T12:00:00+00:00"
    assert states[1]["last_event"] == "2026-10-11T11:30:00+00:00"
    assert states[1]["status"] == "partial"


@pytest.mark.parametrize("invalid", [
    "2026-10-11T12:00:00", "yesterday", "2026-10-11T00:00:00Z secret",
    "X" * 1000, None, 123, {}, [],
])
def test_source_summaries_ignore_untrusted_last_event(invalid):
    class Hostile:
        def __str__(self):
            raise AssertionError("untrusted source metadata stringified")

    payload = {"sources": [
        {"source": "app_log", "status": "unavailable",
         "last_event": invalid},
        {"source": "core_events", "status": "empty",
         "last_event": Hostile()},
    ]}
    states = source_summaries(payload)
    assert "last_event" not in states[0]
    assert "last_event" not in states[1]


def test_last_event_requires_attested_available_or_partial_core_source():
    fixed = "2026-10-11T14:00:00+02:00"
    states = source_summaries({"sources": [
        {"source": "core_events", "status": "available", "last_event": fixed},
        {"source": "app_log", "status": "partial", "last_event": fixed},
        {"source": "app_log", "status": "empty", "last_event": fixed},
        {"source": "core_events", "status": "disabled", "last_event": fixed},
        {"source": "app_log", "status": "unavailable", "last_event": fixed},
        {"source": "Camille Dupont", "status": "available", "last_event": fixed},
    ]})
    assert states[0]["last_event"] == "2026-10-11T12:00:00+00:00"
    assert states[1]["last_event"] == "2026-10-11T12:00:00+00:00"
    assert all("last_event" not in x for x in states[2:])
    assert "Camille Dupont" not in repr(states)


def test_journals_show_last_published_event_from_broker():
    from pathlib import Path
    page = (Path(__file__).parents[1] /
            "cifereca_diagnostics_page.py").read_text(encoding="utf-8")
    helper = (Path(__file__).parents[1] /
              "diagnostics_explorer.py").read_text(encoding="utf-8")
    assert "diagnostic_channel_caption(current_page)" in page
    assert "dernier événement publié :" in helper
    assert 'local_timestamp_label(' in helper
    assert 'state["last_event"]' in helper


def test_log_source_incomplete_reason_is_public_but_free_text_stays_hidden():
    rows = source_summaries({"sources": [
        {"source": "app_log", "status": "partial",
         "reason": "log_source_incomplete",
         "last_event": "2026-10-11T07:00:00+00:00"},
        {"source": "app_log", "status": "partial",
         "reason": "secret-profile-Alexandre",
         "last_event": "2026-10-11T07:00:00+00:00"},
    ]})
    assert rows[0]["reason"] == "log_source_incomplete"
    assert rows[0]["label"] == "Journal partiel (rotation / troncation)"
    assert rows[0]["last_event"] == "2026-10-11T07:00:00+00:00"
    assert "reason" not in rows[1]
    assert "Alexandre" not in json.dumps(rows)


def test_incomplete_snapshot_code_is_shared_without_free_text_provenance():
    from diagnostics_explorer import public_source_evidence
    payload = {"sources": [
        {"source": "core_events", "status": "partial",
         "reason": "snapshot_records_incomplete",
         "last_event": "2026-10-11T09:00:00Z"},
        {"source": "core_events", "status": "partial",
         "reason": "snapshot_records_incomplete secret=Alice Dupont"},
    ]}
    row = source_summaries(payload)
    assert row[0]["reason"] == "snapshot_records_incomplete"
    assert row[0]["status"] == "partial"
    assert row[0]["label"] == "Historique structuré partiel (rejets / limite)"
    assert row[0]["last_event"] == "2026-10-11T09:00:00+00:00"
    assert public_source_evidence(payload["sources"][0])["reason"] == row[0]["reason"]
    assert "reason" not in row[1]
    assert "Alice Dupont" not in json.dumps(row)


def test_diagnostic_channel_caption_explains_two_channels_not_provider_sources():
    caption = diagnostic_channel_caption({
        "sources": [
            {"source": "core_events", "status": "available",
             "last_event": "2026-10-20T17:55:29+00:00"},
            {"source": "app_log", "status": "partial",
             "reason": "log_tail_bounded",
             "last_event": "2026-10-20T17:55:29+00:00"},
        ],
    })
    assert "Canaux de diagnostic" in caption
    assert "Événements Core (mémoire) : Alimentée" in caption
    assert "Journal applicatif (lecture bornée) : Journal partiel" in caption
    assert "Lecture volontairement bornée" in caption
    assert "connexion aux fournisseurs" in caption
    assert "17:55:29" not in caption or "heure locale" in caption
    for forbidden in ("PRIVATE_ACCOUNT", "source Numilog déconnectée"):
        assert forbidden not in caption


def test_channel_caption_distinguishes_disabled_empty_unavailable_from_partial():
    caption = diagnostic_channel_caption({"sources": [
        {"source": "core_events", "status": "disabled",
         "reason": "diagnostics_disabled"},
        {"source": "app_log", "status": "empty",
         "reason": "no_matching_events"},
    ]})
    assert "Désactivée" in caption and "Collecteur Core désactivé" in caption
    assert "Pas d’événement enregistré" in caption
    assert "Aucune ligne technique admissible" in caption
    assert "Indisponible" not in caption


@pytest.mark.parametrize("payload", [
    None, [], "PRIVATE_ACCOUNT", {"sources": "PRIVATE_ACCOUNT"},
    {"sources": []},
])
def test_missing_channel_states_do_not_claim_sources_disconnected(payload):
    caption = diagnostic_channel_caption(payload)
    assert caption == (
        "Canaux de diagnostic : états indisponibles "
        "(événements Core / journal applicatif)."
    )
    assert "PRIVATE" not in caption


def test_channel_caption_refuses_hostile_source_reason_and_timestamp():
    class Private:
        def __str__(self):
            raise AssertionError("must not stringify secret")

    caption = diagnostic_channel_caption({
        "sources": [{
            "source": "PRIVATE_ACCOUNT", "status": "PRIVATE_STATUS",
            "reason": "PRIVATE_TOKEN", "last_event": Private(),
        }, {
            "source": "app_log", "status": "partial",
            "reason": "PRIVATE_HTTP_URL",
            "last_event": "PRIVATE_TIMESTAMP",
        }],
    })
    assert "Canal non identifié : Indisponible" in caption
    assert "Journal applicatif (lecture bornée)" in caption
    for forbidden in ("PRIVATE", "HTTP_URL", "TOKEN", "Timestamp"):
        assert forbidden not in caption


def test_partial_core_memory_is_not_described_as_rotated_application_journal():
    text = diagnostic_channel_caption({"sources": [
        {"source": "core_events", "status": "partial"},
        {"source": "app_log", "status": "partial",
         "reason": "log_tail_bounded"},
    ]})
    assert "Événements Core (mémoire) : Historique Core partiel" in text
    assert "Journal applicatif (lecture bornée) : Journal partiel" in text
    assert "rotation" not in text.split("Journal applicatif", 1)[0]


def test_journal_mode_hides_unsupported_legacy_filters():
    """Journal-only UI must not invite filters the canonical broker rejects."""
    from pathlib import Path

    module = Path(__file__).resolve().parents[1] / "cifereca_diagnostics_page.py"
    source = module.read_text(encoding="utf-8")
    assert "source_selector.set_selected(3 if journal_available else 0)" in source
    for row in ("source_selector", "domain_row", "reason_row"):
        assert ("if not journal_available:\n"
                f"            advanced_box.append({row})") in source


def test_explorer_without_source_explicitly_queries_canonical_journal():
    broker = Broker({
        "events": [], "sources": [{"source": "journal", "status": "empty"}],
        "total": 0, "offset": 0, "next_offset": None,
    })
    fetch_explorer_page(broker, period="all", search="échec")
    assert broker.calls == [{
        "source": "journal", "module": "", "severity": "",
        "since": None, "until": None, "limit": PAGE_SIZE,
        "offset": 0, "search": "échec",
    }]
