"""Exercise full extracted action closures with deterministic toolkit adapters."""
from __future__ import annotations

import os
import stat
import subprocess
import sys
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui.settings_diagnostic_actions import build_settings_diagnostic_callbacks
from cifereca.gui.settings_lifetime import SettingsPageLifetime


def _harness():
    text = ["rapport local brut"]
    buffer = NS(get_bounds=lambda: (0, len(text[0])), get_text=lambda *args: text[0],
                set_text=lambda value: text.__setitem__(0, value))
    clipboard = NS(set_content=Mock(return_value=True), read_text_async=Mock())
    display = NS(get_clipboard=lambda: clipboard)
    gdk = NS(
        Display=NS(get_default=Mock(return_value=display)),
        ContentProvider=NS(new_for_value=Mock(return_value=object())),
    )
    chooser = NS(
        set_current_name=Mock(), connect=Mock(), show=Mock(),
        get_file=Mock(), destroy=Mock(),
    )
    gtk = NS(
        FileChooserNative=NS(new=Mock(return_value=chooser)),
        FileChooserAction=NS(SAVE=1), ResponseType=NS(ACCEPT=7),
    )
    host = NS(
        diagnostics=NS(add=Mock(), clear=Mock()),
        service=NS(save_diagnostic_preferences=Mock()),
        show_error=Mock(), toast=Mock(),
    )
    lifetime = SettingsPageLifetime()
    refresh = Mock()
    level = NS(get_selected=Mock(return_value=1))
    enabled = NS(get_active=Mock(return_value=True))
    actions = build_settings_diagnostic_callbacks(
        host, page_lifetime=lifetime, refresh_text=refresh,
        diag_text=NS(get_buffer=lambda: buffer), level_drop=level,
        diag_switch=enabled, levels=["errors", "normal"], Gtk=gtk, Gdk=gdk,
    )
    return NS(
        host=host, lifetime=lifetime, refresh=refresh, text=text,
        clipboard=clipboard, gdk=gdk, gtk=gtk, chooser=chooser,
        level=level, enabled=enabled,
        copy=actions[0], export=actions[1], persist=actions[2], clear=actions[3],
    )


def test_copy_uses_current_report_and_verifies_original_snapshot_asynchronously():
    h = _harness()
    h.gdk.Display.get_default.assert_not_called()
    h.refresh.side_effect = lambda: h.text.__setitem__(0, "rapport actualisé")
    h.copy()
    h.gdk.ContentProvider.new_for_value.assert_called_once_with("rapport actualisé")
    h.clipboard.set_content.assert_called_once_with(h.host._clipboard_provider)
    h.host.toast.assert_not_called()
    callback = h.clipboard.read_text_async.call_args.args[1]
    h.text[0] = "rapport suivant"
    reader = NS(read_text_finish=Mock(return_value="rapport actualisé"))
    callback(reader, object())
    h.host.toast.assert_called_once_with("Diagnostic copié dans le presse-papiers")
    h.host.show_error.assert_not_called()
    # A queued response from this same action becomes inert on disposal.
    h.lifetime.dispose()
    callback(reader, object())
    assert reader.read_text_finish.call_count == 1


def test_copy_without_graphical_display_preserves_failure_path():
    h = _harness()
    h.gdk.Display.get_default.return_value = None
    h.copy()
    h.host.show_error.assert_called_once_with(
        "Aucun presse-papiers graphique disponible", "Copie impossible",
    )
    h.gdk.ContentProvider.new_for_value.assert_not_called()
    h.clipboard.read_text_async.assert_not_called()


@pytest.mark.parametrize("outcome", ["accept", "cancel", "disposed"])
def test_export_native_response_keeps_snapshot_and_always_destroys_dialog(tmp_path, outcome):
    h = _harness()
    destination = tmp_path / "report.txt"
    h.chooser.get_file.return_value = NS(get_path=lambda: str(destination))
    h.export()
    h.chooser.show.assert_called_once_with()
    signal, callback = h.chooser.connect.call_args.args
    assert signal == "response"
    h.text[0] = "autre rapport"
    if outcome == "disposed":
        h.lifetime.dispose()
    callback(h.chooser, 0 if outcome == "cancel" else 7)
    if outcome == "accept":
        assert destination.read_text(encoding="utf-8") == "rapport local brut"
        assert stat.S_IMODE(destination.stat().st_mode) == 0o600
        h.host.toast.assert_called_once_with("Diagnostic exporté")
    else:
        assert not destination.exists()
        h.chooser.get_file.assert_not_called()
        h.host.toast.assert_not_called()
    h.chooser.destroy.assert_called_once_with()
    h.host.show_error.assert_not_called()


@pytest.mark.parametrize("selection", [-1, 2])
def test_diagnostic_preferences_ignore_out_of_range_selection(selection):
    h = _harness()
    h.level.get_selected.return_value = selection
    h.persist()
    h.host.service.save_diagnostic_preferences.assert_not_called()
    h.enabled.get_active.assert_not_called()
    h.refresh.assert_not_called()


def test_disposed_factory_callbacks_do_not_read_widgets_or_mutate_services():
    h = _harness()
    h.lifetime.dispose()
    for callback in (h.copy, h.export, h.persist, h.clear):
        callback()
    h.gdk.Display.get_default.assert_not_called()
    h.gtk.FileChooserNative.new.assert_not_called()
    h.level.get_selected.assert_not_called()
    h.host.service.save_diagnostic_preferences.assert_not_called()
    h.host.diagnostics.clear.assert_not_called()
    h.refresh.assert_not_called()


def test_callback_module_import_remains_headless():
    code = '''
import importlib.abc, sys
class NoGtk(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'gi' or fullname.startswith('gi.'):
            raise AssertionError('Diagnostics callbacks import GTK')
sys.meta_path.insert(0, NoGtk())
from cifereca.gui.settings_diagnostic_actions import build_settings_diagnostic_callbacks
assert 'cifereca.gui.main_window' not in sys.modules
assert 'gi' not in sys.modules
'''
    result = subprocess.run(
        [sys.executable, "-c", code], env=os.environ.copy(),
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr


PRIVATE = "https://private.invalid/acsm?token=DO_NOT_INCLUDE_IN_DIAGNOSTICS"


def test_clipboard_write_failure_never_logs_exception_or_exposes_secret():
    h = _harness()
    h.clipboard.set_content.side_effect = RuntimeError(PRIVATE)
    h.copy()
    h.host.diagnostics.add.assert_called_once_with(
        "UI", "clipboard write", "error",
        detail={"code": "clipboard_write_failed"}, level="detailed",
    )
    h.host.show_error.assert_called_once_with(
        "Impossible d'écrire dans le presse-papiers", "Copie impossible",
    )
    h.clipboard.read_text_async.assert_not_called()
    assert "DO_NOT_INCLUDE_IN_DIAGNOSTICS" not in str((
        h.host.diagnostics.add.call_args, h.host.show_error.call_args,
    ))


@pytest.mark.parametrize("failure", ["read_finish", "async_schedule"])
def test_clipboard_verification_failures_are_redacted_without_false_copy_failure(failure):
    h = _harness()
    if failure == "async_schedule":
        h.clipboard.read_text_async.side_effect = OSError(PRIVATE)
    h.copy()
    if failure == "read_finish":
        callback = h.clipboard.read_text_async.call_args.args[1]
        callback(NS(read_text_finish=Mock(side_effect=RuntimeError(PRIVATE))), object())
    h.host.diagnostics.add.assert_called_once_with(
        "UI", "clipboard verify", "error",
        detail={"code": "clipboard_verify_failed"}, level="detailed",
    )
    # GDK accepted the write; only verification failed.
    h.host.toast.assert_called_once_with("Diagnostic copié dans le presse-papiers")
    h.host.show_error.assert_not_called()
    assert "DO_NOT_INCLUDE_IN_DIAGNOSTICS" not in str((
        h.host.diagnostics.add.call_args, h.host.show_error.call_args,
    ))


def test_diagnostic_export_never_records_sensitive_local_filename(tmp_path):
    h = _harness()
    secret_filename = "PRIVATE_READER_FILENAME.txt"
    path = tmp_path / secret_filename
    h.chooser.get_file.return_value = NS(get_path=lambda: str(path))
    h.export()
    callback = h.chooser.connect.call_args.args[1]
    callback(h.chooser, h.gtk.ResponseType.ACCEPT)
    assert path.read_text("utf-8") == "rapport local brut"
    h.host.diagnostics.add.assert_called_once_with(
        "UI", "diagnostic export", "ok",
        detail={"code": "export_completed"}, level="detailed",
    )
    assert secret_filename not in str(h.host.diagnostics.add.call_args)


def test_failed_diagnostic_export_never_exposes_selected_private_path(tmp_path):
    h = _harness()
    destination = tmp_path / "PRIVATE_READER_DIRECTORY" / "missing.txt"
    h.chooser.get_file.return_value = NS(get_path=lambda: str(destination))
    h.export()
    callback = h.chooser.connect.call_args.args[1]
    callback(h.chooser, h.gtk.ResponseType.ACCEPT)
    h.chooser.destroy.assert_called_once_with()
    h.host.diagnostics.add.assert_called_once_with(
        "UI", "diagnostic export", "error",
        detail={"code": "diagnostic_export_failed"}, level="detailed",
    )
    h.host.show_error.assert_called_once_with(
        "Impossible d’exporter le diagnostic", "Export impossible",
    )
    assert not destination.exists()
    assert "PRIVATE_READER_DIRECTORY" not in str((
        h.host.diagnostics.add.call_args, h.host.show_error.call_args,
    ))


def test_preferences_save_failure_never_exposes_private_service_exception():
    h = _harness()
    h.host.service.save_diagnostic_preferences.side_effect = RuntimeError(PRIVATE)
    h.persist()
    h.host.show_error.assert_called_once_with(
        "Impossible d’enregistrer le niveau de diagnostic",
    )
    assert PRIVATE not in str(h.host.show_error.call_args)


def test_clear_action_only_clears_presentation_not_persistent_events():
    h = _harness()
    h.clear()
    assert h.text[0] == ""
    h.host.diagnostics.clear.assert_not_called()
    h.refresh.assert_not_called()
    h.host.toast.assert_called_once_with(
        "Affichage effacé ; journal local conservé"
    )


def test_raw_settings_export_renews_private_permissions_on_existing_file(tmp_path):
    h = _harness()
    destination = tmp_path / "old-report.txt"
    destination.write_text("long previous content" * 20)
    os.chmod(destination, 0o644)
    h.chooser.get_file.return_value = NS(get_path=lambda: str(destination))
    h.export()
    callback = h.chooser.connect.call_args.args[1]
    callback(h.chooser, h.gtk.ResponseType.ACCEPT)
    assert destination.read_text() == "rapport local brut"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600


@pytest.mark.skipif(not hasattr(os, "O_NOFOLLOW"), reason="requires O_NOFOLLOW")
def test_raw_settings_export_rejects_symlink_without_touching_target(tmp_path):
    h = _harness()
    outside = tmp_path / "outside.txt"
    outside.write_text("do not alter")
    alias = tmp_path / "report.txt"
    alias.symlink_to(outside)
    h.chooser.get_file.return_value = NS(get_path=lambda: str(alias))
    h.export()
    callback = h.chooser.connect.call_args.args[1]
    callback(h.chooser, h.gtk.ResponseType.ACCEPT)
    assert outside.read_text() == "do not alter"
    assert alias.is_symlink()
    h.host.show_error.assert_called_once_with(
        "Impossible d’exporter le diagnostic", "Export impossible"
    )


def test_raw_export_chooser_warns_before_explicit_user_save():
    h = _harness()
    h.export()
    args = h.gtk.FileChooserNative.new.call_args.args
    assert "BRUT" in args[0] and "mots de passe" in args[0]
