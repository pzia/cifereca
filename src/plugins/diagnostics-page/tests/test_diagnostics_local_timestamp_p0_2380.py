"""P0 #2380: local-clock labels are GUI-only; broker/export stays UTC.

No GTK, provider session, private logs, or remote AI call is involved.
"""
from __future__ import annotations

import os
from pathlib import Path
import time
import json

import pytest

from diagnostics_explorer import local_timestamp_label, export_selected_events


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="POSIX TZ required")
def test_local_labels_preserve_instant_with_summer_and_winter_offsets(monkeypatch):
    original = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "Europe/Paris")
    time.tzset()
    try:
        summer = local_timestamp_label("2026-10-20T11:54:51.293Z")
        same = local_timestamp_label("2026-10-20T13:54:51.293+02:00")
        winter = local_timestamp_label("2026-12-20T11:54:51.293+00:00")
        assert summer == same
        assert summer == "2026-10-20 13:54:51.293+02:00 (heure locale)"
        assert winter == "2026-12-20 12:54:51.293+01:00 (heure locale)"
    finally:
        if original is None:
            monkeypatch.delenv("TZ")
        else:
            monkeypatch.setenv("TZ", original)
        time.tzset()


@pytest.mark.parametrize("bad", [
    None, 123, {}, [], "2026-10-20T13:54:51", "not-a-time",
    "SECRET_URL_https://private.invalid?token=ABC",
    "2026-10-20T11:54:51Z PRIVATE", "X" * 80,
])
def test_bad_timestamps_are_not_repeated_into_gui(bad):
    assert local_timestamp_label(bad) == "Horodatage indisponible"


def test_local_presentation_never_rewrites_utc_export_or_event_reference():
    instant = "2026-10-20T11:54:51.293000+00:00"
    row = {
        "timestamp": instant, "source": "app_log", "module": "cifereca",
        "severity": "info", "message": "TRACE sync source status=ok",
        "event_id": "1234567890abcdef",
    }
    local = local_timestamp_label(instant)
    assert "heure locale" in local
    export = json.loads(export_selected_events([row], {row["event_id"]}))
    assert export["events"][0]["timestamp"] == instant
    assert export["events"][0]["event_id"] == row["event_id"]
    assert "heure locale" not in json.dumps(export)


def test_gtk_renders_explicit_local_label_for_rows_and_source_coverage():
    source = (Path(__file__).resolve().parents[1] /
              "cifereca_diagnostics_page.py").read_text(encoding="utf-8")
    helper = (Path(__file__).resolve().parents[1] /
              "diagnostics_explorer.py").read_text(encoding="utf-8")
    assert "local_timestamp_label(row['timestamp'])" in source
    assert "diagnostic_channel_caption(current_page)" in source
    assert "local_timestamp_label(" in helper and 'state["last_event"]' in helper
    assert "export_selected_events(" in source
