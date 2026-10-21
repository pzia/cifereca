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
        save=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError(PRIVATE)),
    )
    panel.export_directory = Path("/nonexistent-fake-zip-dir")
    panel.export_confirm = SimpleNamespace(get_active=lambda: True)
    panel.export_zip()
    assert seen == [("zip_export", "zip_export_failed")]
    assert messages == ["Enregistrement local du ZIP impossible."]
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
