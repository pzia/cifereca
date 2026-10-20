"""P0 #2141: sync clocks use the display timezone only for aware datetimes."""
from __future__ import annotations

import os
import time
from types import SimpleNamespace

import pytest

from cifereca.gui.sync_view import _timestamp_label, sync_source_phase_summary, sync_source_presentation


@pytest.fixture
def paris_clock(monkeypatch):
    """Portable IANA timezone fixture; restore libc timezone after the test."""
    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset unavailable")
    monkeypatch.setenv("TZ", "Europe/Paris")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@pytest.mark.parametrize(
    ("raw", "display"),
    [
        ("2026-07-01T10:00:00Z", "01/07/2026 12:00"),
        ("2026-01-01T10:00:00+00:00", "01/01/2026 11:00"),
        ("2026-07-01T07:00:00-03:00", "01/07/2026 12:00"),
        ("2026-07-01T19:00:00+09:00", "01/07/2026 12:00"),
        ("2026-07-01T10:00:00", "01/07/2026 10:00"),
    ],
)
def test_known_offsets_are_local_and_naive_clocks_remain_uninterpreted(paris_clock, raw, display):
    assert _timestamp_label(raw) == display


def test_phase_clocks_keep_inventory_and_download_distinct(paris_clock):
    source = SimpleNamespace(
        provider_id="kobo", label="Kobo", enabled=True, connected=True,
        sync_capable=True, status="success", error="",
        last_attempt="2026-10-20T11:55:00Z",
        last_success="2026-10-20T11:55:00Z",
        sync_capabilities=("remote_inventory", "entitlements", "local_materialization"),
        phase_states=(
            SimpleNamespace(
                phase="discover_rights", status="success",
                last_attempt="2026-10-20T11:55:00Z",
                last_success="2026-10-20T11:55:00Z",
            ),
            SimpleNamespace(
                phase="materialize", status="partial",
                last_attempt="2026-10-20T11:54:51Z", last_success="",
            ),
        ),
    )
    rendered = sync_source_presentation(source)
    assert "Dernier succès 20/10/2026 13:55" in rendered.sync_label
    assert "Notices et droits : Réussie · dernier essai 20/10/2026 13:55" in rendered.summary
    assert "Téléchargement local : Partielle · dernier essai 20/10/2026 13:54" in rendered.summary
    assert "Téléchargement local : Partielle · dernier essai 20/10/2026 13:54 · dernier succès" not in rendered.summary


def test_invalid_or_private_timestamp_does_not_leak(monkeypatch, paris_clock):
    secret = "https://example.invalid/?token=SENSITIVE"
    assert _timestamp_label(secret) == ""
    source = SimpleNamespace(phase_states=(SimpleNamespace(
        phase="materialize", status="failed",
        last_attempt=secret, last_success=secret,
    ),))
    assert secret not in sync_source_phase_summary(source)
