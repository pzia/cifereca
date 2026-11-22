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


def test_native_mount_selection_uses_actual_activated_port_not_plugin_name():
    from cifereca.core.activation_scoped_broker import ActivationBrokerResolver
    from cifereca.core.maintenance_read_ports import maintenance_read_ports
    from cifereca.gui.organizer_native_confirmation import has_native_organizer_review
    plugin = object()
    service = NS()
    key = "library.organizer.read"
    resolver = ActivationBrokerResolver(lambda _: None, maintenance_read_ports(service).get,
                                        "fixture.other-organizer", (key,))
    manager = NS(_registry_generation=1, service=lambda _: plugin,
                 _records={"fixture.other-organizer": NS(service=plugin, activation_brokers=resolver)})
    resolved = NS(plugin_id="fixture.other-organizer", service=plugin, registry_generation=1)
    assert not has_native_organizer_review(manager, resolved)
    assert resolver(key) is not None
    assert has_native_organizer_review(manager, resolved)
    assert not has_native_organizer_review(manager, NS(
        plugin_id="maintenance.library.organizer", service=plugin, registry_generation=1))
    resolver.close()
    assert not has_native_organizer_review(manager, resolved)


@pytest.mark.parametrize("failure", ["connect", "present"])
def test_native_dialog_gtk_launch_failure_does_not_consume_review_or_queue_mutation(
    monkeypatch, failure,
):
    Dialog.dialogs = []
    runner = Runner()
    runner.deferred = True
    live = [True]
    window = NS(service=object(), plugin_manager=object(), runner=runner,
                toast=lambda _message: pytest.fail("No result should be reported"))
    lease = NS(identity_id="A", is_current=lambda: live[0])
    page = OrganizerPageAdvice(
        "private-token", 1, 1, 1, (), 0, 0, False, "partial",
        dry_run_state="confirmed",
        dry_run_entries=(OrganizerDryRunAdvice(
            "rename", "old.epub", "new.epub", "review_only"),))
    ui = NativeOrganizerConfirmation(window, object(), lease, Gtk=NS(Button=Widget))
    ui.observe(page)
    assert ui.button.visible and ui.button.sensitive
    method = getattr(Dialog, failure)

    def failed(*_args, **_kwargs):
        raise RuntimeError("PRIVATE_GTK_LAUNCH_ERROR")

    monkeypatch.setattr(Dialog, failure, failed)
    assert ui.open(NS(MessageDialog=Dialog)) is False
    assert ui._dialog is None and ui._page is page and ui._used is None
    assert ui.button.sensitive and not runner.queue
    assert Dialog.dialogs
    if failure == "present":
        # The half-built dialogue may still emit its response asynchronously.
        Dialog.dialogs[-1].respond("apply")
        assert ui._used is None and not runner.queue
    monkeypatch.setattr(Dialog, failure, method)
    assert ui.open(NS(MessageDialog=Dialog)) is True
    Dialog.dialogs[-1].respond("cancel")
    assert ui.button.sensitive and ui._used is None and not runner.queue
