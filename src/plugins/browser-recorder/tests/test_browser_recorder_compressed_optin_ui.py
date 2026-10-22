"""Explicit, one-shot compressed response consent in the Recorder settings."""
from __future__ import annotations

from test_browser_recorder_review_ui import make_panel
import browser_recorder_review_ui as ui


def test_capture_opt_in_is_forwarded_and_reset(monkeypatch):
    panel, tasks, trace = make_panel(monkeypatch)
    seen = []

    def recorder(start, stop, **kwargs):
        assert stop.is_set()
        seen.append(kwargs.get("allow_compressed_responses", False))
        return trace

    monkeypatch.setattr(ui, "record_http", recorder)
    try:
        assert not panel.accept_compressed.get_active()
        panel.accept_compressed.set_active(True)
        panel.begin()
        assert not panel.accept_compressed.get_active()
        assert not panel.accept_compressed.sensitive
        panel.stop_capture()
        tasks.handles[-1].finish()
        assert seen == [True]
        assert panel.accept_compressed.sensitive
    finally:
        panel.destroy()
