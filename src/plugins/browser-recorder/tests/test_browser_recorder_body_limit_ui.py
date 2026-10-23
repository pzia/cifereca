"""Per-capture response-body presets; GTK fake panel, no providers."""
from __future__ import annotations
from test_browser_recorder_review_ui import make_panel
import browser_recorder_review_ui as ui


def test_selected_body_limit_is_one_shot_and_not_implicit(monkeypatch):
    panel, tasks, trace = make_panel(monkeypatch)
    seen = []
    def recorder(start, stop, **kwargs):
        assert stop.is_set()
        seen.append(kwargs.get("max_body", 32768))
        return trace
    monkeypatch.setattr(ui, "record_http", recorder)
    try:
        assert panel.response_body_limit.get_active_id() == "32768"
        panel.response_body_limit.set_active_id("131072")
        panel.begin()
        assert panel.response_body_limit.get_active_id() == "32768"
        assert not panel.response_body_limit.sensitive
        panel.stop_capture()
        tasks.handles[-1].finish()
        assert seen == [131072]
        assert panel.response_body_limit.sensitive
    finally:
        panel.destroy()
