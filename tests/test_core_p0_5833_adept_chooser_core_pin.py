"""#5833/#5975: queued ADEPT profile choices never drift to a foreign Core."""
from __future__ import annotations

from types import SimpleNamespace as NS
from unittest.mock import Mock

from cifereca.gui.settings_chooser_controller import choose_adept_profile
from tests.test_core_ui_1132_settings_chooser_controller import _adept_host


def test_queued_profile_inventory_method_and_callback_remain_core_bound():
    window, entry, control, adw, errors, dialogs, submitted = _adept_host()
    admitted = window.service
    choose_adept_profile(window, entry, control, Adw=adw)
    args, _ = submitted[0]
    worker, done, failed = args[1:4]

    foreign_choices = Mock(side_effect=AssertionError("foreign Core read"))
    window.service = NS(adept_profile_choices=foreign_choices)
    # The worker is a bound callable captured on the original Core, while
    # the current GTK view has already been replaced.
    assert worker is admitted.adept_profile_choices
    worker()
    admitted.adept_profile_choices.assert_called_once_with()
    foreign_choices.assert_not_called()
    done([{"name": "Secret", "reference": "private-adobe-profile"}])
    failed(RuntimeError("private source failed"))
    assert dialogs == []
    assert errors == []
    entry.set_text.assert_not_called()


def test_chooser_cannot_publish_after_same_core_method_rebinding():
    window, entry, control, adw, errors, dialogs, submitted = _adept_host()
    admitted = window.service
    choose_adept_profile(window, entry, control, Adw=adw)
    original_method = submitted[0][0][1]
    admitted.adept_profile_choices = Mock(
        side_effect=AssertionError("late rebound method must not execute"),
    )
    original_method()
    admitted.adept_profile_choices.assert_not_called()
    submitted[0][0][2]([{"name": "Admis", "reference": "old-profile"}])
    assert len(dialogs) == 1
    dialogs[0].respond("profile-0")
    entry.set_text.assert_called_once_with("old-profile")


def test_already_open_profile_dialog_rejects_selection_after_core_replacement():
    window, entry, control, adw, errors, dialogs, submitted = _adept_host()
    choose_adept_profile(window, entry, control, Adw=adw)
    submitted[0][0][2]([{"name": "Adobe", "reference": "old-core-profile"}])
    assert len(dialogs) == 1
    window.service = NS(adept_profile_choices=Mock())
    dialogs[0].respond("profile-0")
    entry.set_text.assert_not_called()
    assert errors == []
