"""CoreUI #1347: Settings may navigate to source-scoped previews, never acquire.

The old Settings field name remains for compatibility, but its button is
strictly navigation-only. These headless tests exercise the real callbacks.
"""
from __future__ import annotations

from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui.settings_lifetime import SettingsPageLifetime
from cifereca.gui.settings_model import SettingsRevisionState
from cifereca.gui.settings_sync_actions import bind_settings_sync_actions
from cifereca.gui.settings_sync_section import kobo_backup_action_state


class Control:
    def __init__(self, *, active=False):
        self.active, self.sensitive, self.subtitle = active, None, ""
        self.handlers = {}

    def get_active(self):
        return self.active

    def set_sensitive(self, value):
        self.sensitive = value

    def set_subtitle(self, value):
        self.subtitle = value

    def connect(self, signal, callback):
        self.handlers.setdefault(signal, []).append(callback)

    def emit(self, signal):
        for callback in tuple(self.handlers.get(signal, ())):
            callback(self)


def _case(configured=True):
    controls = NS(**{
        key: Control(active=(configured is True) if key == "backup_switch" else False)
        for key in (
            "backup_switch", "backup_dir_row",
            "backup_interval_row", "vault_profile_drop",
            "run_backup_now", "backup_status",
        )
    })
    host = NS(
        _closed=False,
        config={"kobo_backup.enabled": configured},
        navigate=Mock(),
        run_kobo_backup=Mock(side_effect=AssertionError("global acquisition forbidden")),
    )
    lifetime = SettingsPageLifetime()
    changes = SettingsRevisionState(["sync"])
    refresh = bind_settings_sync_actions(
        host, page_lifetime=lifetime, settings_state=changes,
        kobo_settings=controls,
    )
    return host, controls, lifetime, changes, refresh


@pytest.mark.parametrize("configured", [True, False])
def test_navigation_to_sync_does_not_materialize_even_when_backup_enabled(configured):
    host, ui, _, _, _ = _case(configured)
    assert ui.run_backup_now.sensitive is True
    ui.run_backup_now.emit("clicked")
    host.navigate.assert_called_once_with("sync")
    host.run_kobo_backup.assert_not_called()


@pytest.mark.parametrize("corrupt", ["true", "false", 1, 0, None, [], {}])
def test_corrupt_opt_in_never_authorizes_local_download_or_changes_settings(corrupt):
    host, ui, _, _, _ = _case(corrupt)
    previous = host.config["kobo_backup.enabled"]
    ui.run_backup_now.emit("clicked")
    # The destination is always the per-identity read-only-capable Sync page.
    host.navigate.assert_called_once_with("sync")
    host.run_kobo_backup.assert_not_called()
    assert host.config["kobo_backup.enabled"] is previous


def test_unsaved_switch_blocks_navigation_and_global_download():
    host, ui, _, state, refresh = _case(True)
    state.mark_dirty("sync")
    ui.backup_switch.active = False
    ui.backup_switch.emit("notify::active")
    assert ui.run_backup_now.sensitive is False
    assert "enregistrer" in ui.backup_status.subtitle
    ui.run_backup_now.emit("clicked")
    host.navigate.assert_not_called()
    host.run_kobo_backup.assert_not_called()
    refresh()
    assert ui.run_backup_now.sensitive is False


def test_disposed_page_rejects_late_click_and_refresh():
    host, ui, lifetime, _, refresh = _case(True)
    lifetime.dispose()
    before = ui.backup_status.subtitle
    ui.run_backup_now.emit("clicked")
    refresh()
    assert ui.backup_status.subtitle == before
    host.navigate.assert_not_called()
    host.run_kobo_backup.assert_not_called()


def test_closed_window_rejects_navigation_even_if_button_was_enabled():
    host, ui, _, _, _ = _case(True)
    assert ui.run_backup_now.sensitive is True
    host._closed = True
    ui.run_backup_now.emit("clicked")
    host.navigate.assert_not_called()
    host.run_kobo_backup.assert_not_called()


def test_navigation_reentrant_disposal_never_starts_background_work():
    host, ui, lifetime, _, _ = _case(True)
    host.navigate.side_effect = lambda route: lifetime.dispose()
    ui.run_backup_now.emit("clicked")
    ui.run_backup_now.emit("clicked")
    host.navigate.assert_called_once_with("sync")
    host.run_kobo_backup.assert_not_called()


def test_navigation_readiness_cannot_be_reused_as_download_authorization():
    for persisted, draft, dirty, allowed in (
        (True, True, False, True), (False, False, False, True),
        (True, False, False, False), (True, True, True, False),
        (False, True, False, False), ("true", True, False, False),
        (1, True, False, False),
    ):
        label, ready = kobo_backup_action_state(
            persisted_enabled=persisted, draft_enabled=draft, dirty=dirty,
        )
        assert ready is allowed
        assert isinstance(label, str) and label
        assert "télécharger maintenant" not in label.lower()


def test_sync_navigation_does_not_depend_on_obsolete_kobo_switch_value():
    """#3731: provider-neutral Sync stays accessible without Kobo consent."""
    host, ui, _, _, refresh = _case(True)
    ui.backup_switch.active = False  # stale legacy display, no unsaved change
    refresh()
    assert ui.run_backup_now.sensitive is True
    ui.run_backup_now.emit("clicked")
    host.navigate.assert_called_once_with("sync")
    host.run_kobo_backup.assert_not_called()
    assert host.config["kobo_backup.enabled"] is True


def test_sync_navigation_never_starts_download_for_unknown_provider():
    host, ui, _, _, _ = _case(False)
    host.config = {"providers.accounts": [{"id": "future", "provider": "future-v2", "enabled": False}]}
    ui.run_backup_now.emit("clicked")
    host.navigate.assert_called_once_with("sync")
    host.run_kobo_backup.assert_not_called()
