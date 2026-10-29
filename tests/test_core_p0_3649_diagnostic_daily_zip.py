"""#3649: real canonical log reader + private day-only ZIP, no network/GTK.

All timestamps, logs and credentials are synthetic. The process TZ is
temporarily changed so canonical Formatter wall timestamps are interpreted
the same way as the requested IANA local day.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import time
from zipfile import ZipFile

import pytest

from cifereca.core.diagnostic_journal_zip import export_daily_journal_zip


@pytest.fixture
def paris_clock(monkeypatch):
    original = os.environ.get("TZ")
    monkeypatch.setenv("TZ", "Europe/Paris")
    if hasattr(time, "tzset"):
        time.tzset()
    try:
        yield
    finally:
        if original is None:
            monkeypatch.delenv("TZ", raising=False)
        else:
            monkeypatch.setenv("TZ", original)
        if hasattr(time, "tzset"):
            time.tzset()


def row(day, hour, body, *, level="INFO"):
    return f"{day} {hour},000 {level} cifereca [Worker] {body}\n"


def inspect_zip(path):
    with ZipFile(path) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == {
            "cifereca-journal-brut.json", "manifest.json", "README.txt",
        }
        raw = archive.read("cifereca-journal-brut.json")
        manifest = json.loads(archive.read("manifest.json"))
        assert sha256(raw).hexdigest() == manifest["sha256"]
        assert manifest["raw_bytes"] == len(raw)
        return json.loads(raw), manifest


def test_today_is_exact_local_half_open_range_across_rotations(tmp_path, paris_clock):
    log = tmp_path / "cifereca.log"
    (tmp_path / "cifereca.log.1").write_text(
        row("2026-10-28", "23:59:59", "old login=alice password=SYNTHETIC")
        + row("2026-10-29", "00:00:00", "today-start"),
        encoding="utf-8",
    )
    log.write_text(
        row("2026-10-29", "06:59:23", "today-middle token=SYNTHETIC")
        + row("2026-10-29", "23:59:59", "today-end", level="ERROR")
        + row("2026-10-30", "00:00:00", "tomorrow"),
        encoding="utf-8",
    )
    output = tmp_path / "today.zip"
    path = export_daily_journal_zip(
        log, output, now=datetime(2026, 10, 29, 6, tzinfo=timezone.utc),
        zone_name="Europe/Paris",
    )
    raw, manifest = inspect_zip(path)
    assert [e["message"] for e in raw["events"]] == [
        "today-start", "today-middle token=SYNTHETIC", "today-end",
    ]
    assert [e["severity"] for e in raw["events"]] == ["info", "info", "error"]
    assert manifest["event_count"] == 3
    assert manifest["date_local"] == "2026-10-29"
    assert manifest["timezone"] == "Europe/Paris"
    assert manifest["utc_start"] == "2026-10-28T23:00:00+00:00"
    assert manifest["utc_end_exclusive"] == "2026-10-29T23:00:00+00:00"
    assert manifest["coverage"] == "available" and raw["atomic"] is False
    assert manifest["ui_filters_applied"] is False
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert list(tmp_path.glob(".cifereca-journal-*.part")) == []


@pytest.mark.parametrize(("day", "start", "end", "hours"), [
    ("2026-03-29", "2026-03-28T23:00:00+00:00",
     "2026-03-29T22:00:00+00:00", 23),
    ("2026-10-25", "2026-10-24T22:00:00+00:00",
     "2026-10-25T23:00:00+00:00", 25),
])
def test_dst_boundaries_are_not_fixed_24_hour_windows(
    tmp_path, paris_clock, day, start, end, hours,
):
    log = tmp_path / "cifereca.log"
    log.write_text(row(day, "12:00:00", "normal"), encoding="utf-8")
    output = tmp_path / "dst.zip"
    _, manifest = inspect_zip(export_daily_journal_zip(
        log, output, now=datetime.fromisoformat(start),
        zone_name="Europe/Paris",
    ))
    assert manifest["date_local"] == day
    assert manifest["utc_start"] == start
    assert manifest["utc_end_exclusive"] == end
    assert (datetime.fromisoformat(end) - datetime.fromisoformat(start)
            ).total_seconds() == hours * 3600


def test_raw_diagnostics_and_credentials_are_not_pseudonymized(
    tmp_path, paris_clock,
):
    log = tmp_path / "cifereca.log"
    original = (
        "login=real@example.test password=raw-credential token=unchanged "
        "https://example.test/signed?signature=SYNTHETIC"
    )
    log.write_text(
        row("2026-10-29", "06:58:00", original, level="WARNING"),
        encoding="utf-8",
    )
    raw, manifest = inspect_zip(export_daily_journal_zip(
        log, tmp_path / "plain.zip",
        now=datetime(2026, 10, 29, 7, tzinfo=timezone.utc),
        zone_name="Europe/Paris",
    ))
    assert raw["events"][0]["message"] == original
    assert manifest["event_count"] == 1


@pytest.mark.parametrize("destination_kind", ["existing", "symlink"])
def test_destination_is_never_overwritten(
    tmp_path, paris_clock, destination_kind,
):
    log = tmp_path / "cifereca.log"
    log.write_text(row("2026-10-29", "08:00:00", "keep"), encoding="utf-8")
    output = tmp_path / "journal.zip"
    untouched = tmp_path / "untouched.zip"
    untouched.write_bytes(b"ORIGINAL")
    if destination_kind == "existing":
        output.write_bytes(b"ORIGINAL")
    else:
        output.symlink_to(untouched)
    with pytest.raises(FileExistsError):
        export_daily_journal_zip(
            log, output, now=datetime(2026, 10, 29, tzinfo=timezone.utc),
            zone_name="Europe/Paris",
        )
    assert untouched.read_bytes() == b"ORIGINAL"
    assert not list(tmp_path.glob(".cifereca-journal-*.part"))


def test_concurrent_exports_publish_only_one_complete_zip(tmp_path, paris_clock):
    log = tmp_path / "cifereca.log"
    log.write_text(row("2026-10-29", "08:00:00", "keep"), encoding="utf-8")
    output = tmp_path / "journal.zip"
    def export(_):
        try:
            export_daily_journal_zip(
                log, output, now=datetime(2026, 10, 29, tzinfo=timezone.utc),
                zone_name="Europe/Paris",
            )
            return "ok"
        except FileExistsError:
            return "exists"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(export, range(2)))
    assert sorted(results) == ["exists", "ok"]
    assert inspect_zip(output)[1]["event_count"] == 1
    assert not list(tmp_path.glob(".cifereca-journal-*.part"))


def test_cancel_mid_zip_never_leaves_destination_or_temp(tmp_path, paris_clock):
    log = tmp_path / "cifereca.log"
    log.write_text("".join(
        row("2026-10-29", "08:00:00", f"record {i}")
        for i in range(200)
    ), encoding="utf-8")
    output = tmp_path / "cancel.zip"
    count = 0
    def cancelled():
        nonlocal count
        count += 1
        return count > 15
    with pytest.raises(InterruptedError, match="journal_export_cancelled"):
        export_daily_journal_zip(
            log, output, now=datetime(2026, 10, 29, tzinfo=timezone.utc),
            zone_name="Europe/Paris", cancelled=cancelled,
        )
    assert not output.exists()
    assert not list(tmp_path.glob(".cifereca-journal-*.part"))


def test_discontinuous_rotation_is_marked_partial_not_complete(
    tmp_path, paris_clock,
):
    log = tmp_path / "cifereca.log"
    log.write_text(row("2026-10-29", "09:00:00", "recent"), encoding="utf-8")
    (tmp_path / "cifereca.log.2").write_text(
        row("2026-10-29", "08:00:00", "old"),
        encoding="utf-8",
    )
    payload, manifest = inspect_zip(export_daily_journal_zip(
        log, tmp_path / "partial.zip",
        now=datetime(2026, 10, 29, tzinfo=timezone.utc),
        zone_name="Europe/Paris",
    ))
    assert manifest["coverage"] == payload["coverage"] == "partial"
    assert "rotation_1_missing" in manifest["rotation_gaps"]
    assert manifest["event_count"] == 2


def test_unresolvable_zone_and_failed_publish_leave_no_zip(tmp_path, paris_clock, monkeypatch):
    import cifereca.core.diagnostic_journal_zip as export_module
    log = tmp_path / "cifereca.log"
    log.write_text(row("2026-10-29", "09:00:00", "old"), encoding="utf-8")
    with pytest.raises(ValueError, match="journal_timezone_unavailable"):
        export_daily_journal_zip(log, tmp_path / "missing.zip",
                                 zone_name="NoSuch/Zone")
    monkeypatch.setattr(export_module.os, "link", lambda *_a, **_kw: (
        _ for _ in ()).throw(OSError("simulated disk full")
    ))
    with pytest.raises(OSError):
        export_daily_journal_zip(
            log, tmp_path / "disk.zip",
            now=datetime(2026, 10, 29, tzinfo=timezone.utc),
            zone_name="Europe/Paris",
        )
    assert not (tmp_path / "disk.zip").exists()
    assert not list(tmp_path.glob(".cifereca-journal-*.part"))
