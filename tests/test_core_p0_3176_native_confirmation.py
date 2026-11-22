"""#3176 native GTK consent callback; actual Core worker recipe in #7544."""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core import library_organizer_execution as execution
from cifereca.gui.organizer_native_confirmation import NativeOrganizerConfirmation
from cifereca.plugin_api.library_organizer import OrganizerPageAdvice, OrganizerDryRunAdvice
from tests.test_library_declaration_actions import Widget, Dialog, Runner


@pytest.mark.parametrize("case", ["apply", "cancel", "stale_dialog", "stale_worker", "other_plan", "prepared"])
def test_native_dialog_admits_only_exact_confirmed_single_rename(monkeypatch, case):
    Dialog.dialogs = []
    runner = Runner()
    runner.deferred = True
    calls, messages = [], []
    live = [True]
    service, manager, resolved = object(), object(), object()
    window = NS(service=service, plugin_manager=manager, runner=runner, toast=messages.append)
    lease = NS(identity_id="A", is_current=lambda: live[0])
    page = OrganizerPageAdvice(
        "private-token", 1, 1, 1, (), 0, 0, False, "partial",
        dry_run_state="confirmed",
        dry_run_entries=(OrganizerDryRunAdvice("rename", "old.epub", "new.epub", "review_only"),))
    def admit(m, r, p, **kwargs):
        assert m is manager and r is resolved and p is page
        assert kwargs["service"] is service and kwargs["identity_id"] == "A"
        assert kwargs["confirmed"] is True and kwargs["view_current"]()
        calls.append(p)
        return lambda: {"status": "blocked", "changed": 0}
    monkeypatch.setattr(execution, "admit_native_reviewed_rename", admit, raising=False)
    ui = NativeOrganizerConfirmation(window, resolved, lease, Gtk=NS(Button=Widget))
    assert not ui.button.visible and not runner.queue
    if case == "other_plan":
        ui.observe(replace(page, dry_run_entries=(replace(page.dry_run_entries[0], operation="keeper"),)))
    elif case == "prepared":
        ui.observe(replace(page, dry_run_state="prepared"))
    else:
        ui.observe(page)
    if case in ("other_plan", "prepared"):
        assert not ui.button.visible and not ui.open(NS(MessageDialog=Dialog))
        assert not Dialog.dialogs and not calls
        return
    assert ui.button.visible and ui.open(NS(MessageDialog=Dialog))
    dialog, = Dialog.dialogs
    assert dialog.default == dialog.close_response == "cancel" and not dialog.markup
    assert "old.epub" in dialog.body and "new.epub" in dialog.body and page.token not in dialog.body
    assert not runner.queue and not calls
    if case == "stale_dialog":
        ui.observe(None)
    dialog.respond("cancel" if case == "cancel" else "apply")
    dialog.respond("apply")
    if case in ("apply", "stale_worker"):
        assert len(runner.queue) == 1 and not calls
        if case == "stale_worker":
            live[0] = False
        runner.run()
    assert calls == ([page] if case == "apply" else [])
    assert not runner.queue
    if case == "apply":
        assert not ui.open(NS(MessageDialog=Dialog))
        assert "refusé" in messages[0]
    else:
        assert not messages
