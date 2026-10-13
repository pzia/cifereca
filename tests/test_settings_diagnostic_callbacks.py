"""Exercise full extracted action closures with deterministic toolkit adapters."""
from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui.settings_diagnostic_actions import build_settings_diagnostic_callbacks
from cifereca.gui.settings_lifetime import SettingsPageLifetime


def _harness():
    text = ["rapport synthétique expurgé"]
    buffer = NS(get_bounds=lambda: (0, len(text[0])), get_text=lambda *args: text[0])
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
        assert destination.read_text(encoding="utf-8") == "rapport synthétique expurgé"
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
