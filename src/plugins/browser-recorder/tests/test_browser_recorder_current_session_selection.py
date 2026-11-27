"""Regression #2884: latest sealed vault session selectable before next capture.

Synthetic encrypted sessions only. No browser or provider traffic.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as NS

import pytest

pytest.importorskip("cryptography")

from browser_recorder_http import PrivateExchange, PrivateHttpTrace
import browser_recorder_review_ui as view
from test_browser_recorder_review_ui import FakeTasks, Gtk, GLib, ORIGIN, new_test_vault


SECRET = "FAKE-SELECTOR-VAULT-PASSPHRASE-2026"


def test_newest_sealed_capture_is_immediately_selected_and_older_stays_readable(
    monkeypatch, tmp_path: Path,
):
    tasks = FakeTasks()
    core, vault = new_test_vault(tmp_path)
    panel = view.PrivateReviewPanel(
        Gtk, GLib, tasks, NS(get_text=lambda: ORIGIN + "/catalog"),
        NS(get_active=lambda: True), lambda: False,
        vault=vault,
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
            panel.begin()
            panel.stop_capture()
            tasks.handles[-1].finish()
            tasks.handles[-1].finish()  # authenticated index scan
            assert len(numbers) == number
            sid = numbers[-1]
            assert panel.session_list.get_active_id() == sid
            assert panel.session_list.value == sid
            stored = vault.read(sid)
            assert stored.trace.exchanges[0].response_body == '{"BookId":214}'
            stored.trace.destroy()
            assert len(vault.sessions()) == number
        assert numbers[0] != numbers[1]
        # Manual "Lister" may not override explicit user selection.
        panel.session_list.set_active_id(numbers[0])
        panel.refresh_sessions()
        tasks.handles[-1].finish()
        assert panel.session_list.get_active_id() == numbers[0]
        assert all("completed" in value for value in panel.session_list.options.values())
        assert "sessions chiffrées" in panel.status.label

        # An additional capture does not mutate/decrypt the preceding files.
        first = vault.read(numbers[0])
        try:
            assert first.trace.exchanges[0].url.endswith("/1")
        finally:
            first.trace.destroy()
    finally:
        panel.destroy()
        core.close()
