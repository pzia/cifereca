"""P0 #2663: HTTP/ZIP failures publish codes, never a private exception.

Synthetic local objects only. No GTK, browser, filesystem export or network.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from browser_recorder_review_ui import PrivateReviewPanel, _capture_diagnostic_code


PRIVATE = "https://fiction.invalid/?token=FAKE_SECRET_HTTP_ZIP"


def panel_fixture():
    seen = []
    messages = []
    panel = object.__new__(PrivateReviewPanel)
    panel._closed = False
    panel._export_dialog = None
    panel._generation = 0
    panel._diagnostic = lambda op, code: seen.append((op, code))
    panel.is_recording = lambda: False
    panel.status = SimpleNamespace(set_label=messages.append)
    return panel, seen, messages


def test_prepare_zip_exception_emits_only_fixed_failure(monkeypatch):
    panel, seen, messages = panel_fixture()
    panel._review = SimpleNamespace(pending=lambda: 0)
    panel._invalidate_bundle = lambda: None

    def fail(_review):
        raise RuntimeError(PRIVATE)

    monkeypatch.setattr("browser_recorder_review_ui.prepare_reviewed_zip", fail)
    panel.prepare_zip()
    assert seen == [("zip_prepare", "zip_prepare_failed")]
    assert messages == ["Archive refusée : risque ou limite de contenu."]
    assert "FAKE_SECRET_HTTP_ZIP" not in repr((seen, messages))


def test_export_zip_exception_emits_only_fixed_failure():
    panel, seen, messages = panel_fixture()
    panel._bundle_plan = SimpleNamespace(
        save_as=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError(PRIVATE)),
    )
    panel.export_directory = Path("/nonexistent-fake-zip-dir")
    panel.export_confirm = SimpleNamespace(get_active=lambda: True)
    panel._prompt_export_file = lambda chosen: chosen(
        Path("/nonexistent-fake-zip-dir/chosen.zip")
    )
    panel.export_zip()
    assert seen == [("zip_export", "zip_export_failed")]
    assert "Enregistrement refusé" in messages[-1]
    assert "FAKE_SECRET_HTTP_ZIP" not in repr((seen, messages))


def test_without_confirmation_or_diagnostics_no_secret_is_reported():
    panel, seen, messages = panel_fixture()
    panel._bundle_plan = SimpleNamespace()
    panel.export_directory = Path("/nonexistent-fake-zip-dir")
    panel.export_confirm = SimpleNamespace(get_active=lambda: False)
    panel.export_zip()
    assert seen == []
    assert "confirmation manquante" in messages[-1].lower()

    panel._diagnostic = None
    panel._emit_failure("http_capture", "capture_failed")
    panel._diagnostic = lambda *_: (_ for _ in ()).throw(RuntimeError(PRIVATE))
    panel._emit_failure("http_capture", "capture_failed")
    assert seen == []


@pytest.mark.parametrize("code", ["capture_failed", "capture_interrupted",
                                    "capture_limit", "playwright_missing"])
def test_closed_failure_callback_uses_no_private_exception(code):
    panel, seen, messages = panel_fixture()
    panel._emit_failure("http_capture", code)
    assert seen == [("http_capture", code)]
    assert PRIVATE not in repr((seen, messages))


def test_hostile_recorder_exception_argument_never_invokes_magic_methods():
    class Hostile:
        def __eq__(self, _other):
            raise AssertionError("comparison could leak site-controlled data")
        def __repr__(self):
            raise AssertionError("representation could leak session details")
        def __str__(self):
            raise AssertionError("coercion could leak session details")

    assert _capture_diagnostic_code(Hostile()) == "capture_failed"
    assert _capture_diagnostic_code("playwright_unavailable") == "playwright_missing"
    assert _capture_diagnostic_code("capture_limit") == "capture_limit"
    assert _capture_diagnostic_code(PRIVATE) == "capture_failed"


def _private_export_panel(tmp_path):
    from test_browser_recorder_private_zip import stored, PASSPHRASE
    vault, source = stored(tmp_path)
    panel, _, messages = panel_fixture()
    events = []
    panel._diagnostic = lambda op, code, **metrics: events.append((op, code, metrics))
    panel.vault_directory = vault.path
    panel.private_export_confirm = SimpleNamespace(get_active=lambda: True, set_active=lambda _: None)
    panel.session_list = SimpleNamespace(get_active_id=lambda: source.session_id)
    destination = tmp_path / "private-export"
    destination.mkdir(mode=0o700)
    panel.private_export_directory = SimpleNamespace(get_text=lambda: str(destination))
    panel.vault_passphrase = SimpleNamespace(get_text=lambda: PASSPHRASE, set_text=lambda _: None)
    panel.private_export_passphrase = SimpleNamespace(
        get_text=lambda: "FICTIONAL-SEPARATE-DIAGNOSTIC-EXPORT", set_text=lambda _: None,
    )
    panel._refresh = lambda: None
    return panel, events, messages, destination, source


def test_private_export_reports_size_and_duration_only_after_explicit_success(tmp_path):
    panel, events, messages, folder, source = _private_export_panel(tmp_path)
    try:
        panel.private_export_confirm.get_active = lambda: False
        panel.export_private_zip()
        assert not events and not list(folder.iterdir())
        panel.private_export_confirm.get_active = lambda: True
        panel.export_private_zip()
        assert len(events) == 1
        op, code, metrics = events[0]
        assert (op, code) == ("private_zip_export", "private_zip_exported")
        assert set(metrics) == {"duration_ms", "output_bytes"}
        assert type(metrics["duration_ms"]) is int and metrics["duration_ms"] >= 0
        assert metrics["output_bytes"] == next(folder.glob("*.brzip")).stat().st_size
        assert str(folder) not in repr(events)
        assert "FICTIONAL-SEPARATE" not in repr((events, messages))
    finally:
        source.trace.destroy()


def test_private_export_failure_reports_one_fixed_terminal_without_exception(tmp_path, monkeypatch):
    panel, events, messages, folder, source = _private_export_panel(tmp_path)
    def fail(*_a, **_kw):
        raise OSError(PRIVATE)
    monkeypatch.setattr("browser_recorder_review_ui.save_private_zip", fail)
    try:
        panel.export_private_zip()
        assert len(events) == 1
        assert events[0][:2] == ("private_zip_export", "private_zip_export_failed")
        assert events[0][2]["output_bytes"] == 0
        assert not list(folder.iterdir())
        assert "FAKE_SECRET_HTTP_ZIP" not in repr((events, messages))
    finally:
        source.trace.destroy()


def test_cancelled_private_folder_dialog_has_one_safe_event_without_export():
    panel, _, messages = panel_fixture()
    events = []
    panel._diagnostic = lambda op, code, **metrics: events.append((op, code, metrics))
    class Dialog:
        def connect(self, _signal, callback):
            self.callback = callback
        def show(self):
            pass
        def destroy(self):
            pass
    dialog = Dialog()
    panel.Gtk = SimpleNamespace(
        Window=type("Window", (), {}),
        FileChooserNative=SimpleNamespace(new=lambda *_: dialog),
        FileChooserAction=SimpleNamespace(SELECT_FOLDER=1),
        ResponseType=SimpleNamespace(ACCEPT=1),
    )
    panel.widget = SimpleNamespace(get_root=lambda: None)
    panel._private_destination_dialog = None
    panel._prompt_private_folder()
    dialog.callback(dialog, 0)
    assert panel._private_destination_dialog is None
    assert len(events) == 1
    assert events[0][:2] == ("private_zip_export", "private_zip_export_cancelled")
    assert events[0][2]["output_bytes"] == 0
    assert not messages


def test_page_callback_preserves_old_failures_and_private_metric_types():
    from browser_recorder_page import BrowserRecorderPage
    seen = []
    page = BrowserRecorderPage()
    page.context = SimpleNamespace(report_diagnostic=lambda *a, **kw: seen.append((a, kw)))
    page._report_http_outcome("http_review", "review_failed")
    assert seen[0][0][1] == "error"
    assert seen[0][1]["detail"] == {"operation": "http_review", "code": "review_failed"}
    page._report_http_outcome("private_zip_export", "private_zip_exported", duration_ms=4, output_bytes=12)
    assert seen[1][0][1] == "ok"
    assert seen[1][1]["detail"]["output_bytes"] == 12
    page._report_http_outcome("private_zip_export", "private_zip_export_cancelled", duration_ms=True, output_bytes=PRIVATE)
    assert seen[2][0][1] == "warning"
    assert set(seen[2][1]["detail"]) == {"operation", "code"}
    assert PRIVATE not in repr(seen)
