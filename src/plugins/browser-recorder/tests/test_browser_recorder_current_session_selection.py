"""Regression #2884: latest sealed vault session selectable before next capture.

Synthetic encrypted sessions only. No browser or provider traffic.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as NS

import pytest

pytest.importorskip("cryptography")

from browser_recorder_http import PrivateExchange, PrivateHttpTrace
from browser_recorder_vault import PrivateSessionVault
import browser_recorder_review_ui as view
from test_browser_recorder_review_ui import FakeTasks, Gtk, GLib, ORIGIN


SECRET = "FAKE-SELECTOR-VAULT-PASSPHRASE-2026"


def test_newest_sealed_capture_is_immediately_selected_and_older_stays_readable(
    monkeypatch, tmp_path: Path,
):
    tasks = FakeTasks()
    vault_path = tmp_path / "vault"
    panel = view.PrivateReviewPanel(
        Gtk, GLib, tasks, NS(get_text=lambda: ORIGIN + "/catalog"),
        NS(get_active=lambda: True), lambda: False,
        vault_directory=vault_path,
    )
    numbers = []

    def recorder(start, stop, **kwargs):
        assert start.startswith(ORIGIN) and stop.is_set()
        assert "vault_writer" in kwargs
        trace = PrivateHttpTrace((ORIGIN,))
        n = len(numbers) + 1
        trace.exchanges.append(PrivateExchange(
            1, "GET", url=ORIGIN + "/fake/" + str(n),
            status=200, mime="application/json",
            response_body='{"BookId":214}', response_body_state="captured",
            response_body_reason="captured",
        ))
        kwargs["vault_writer"].save(trace, "completed", force=True)
        numbers.append(trace.vault_session_id)
        return trace

    monkeypatch.setattr(view, "record_http", recorder)
    try:
        for number in (1, 2):
            panel.vault_consent.set_active(True)
            panel.vault_passphrase.set_text(SECRET)
            panel.begin()
            panel.stop_capture()
            tasks.handles[-1].finish()
            assert len(numbers) == number
            sid = numbers[-1]
            assert panel.session_list.get_active_id() == sid
            assert panel.session_list.value == sid
            stored = PrivateSessionVault(vault_path).open(sid, SECRET)
            assert stored.trace.exchanges[0].response_body == '{"BookId":214}'
            stored.trace.destroy()
            assert len(PrivateSessionVault(vault_path).list_ids()) == number
        assert numbers[0] != numbers[1]
        # Manual "Lister" may not override explicit user selection.
        panel.session_list.set_active_id(numbers[0])
        panel.refresh_sessions()
        assert panel.session_list.get_active_id() == numbers[0]
        assert all("Archive chiffrée" in value
                   for _key, value in panel.session_list.options.items())
        assert "non chronologique" in panel.status.label

        # An additional capture does not mutate/decrypt the preceding files.
        first = PrivateSessionVault(vault_path).open(numbers[0], SECRET)
        try:
            assert first.trace.exchanges[0].url.endswith("/1")
        finally:
            first.trace.destroy()
    finally:
        panel.destroy()
