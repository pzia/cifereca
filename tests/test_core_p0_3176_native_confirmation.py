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


@pytest.mark.parametrize("phase", [
    "new", "set_body_use_markup", "add_response",
    "set_default_response", "set_close_response",
])
def test_native_dialog_builder_failure_is_retryable_without_worker(
    monkeypatch, phase,
):
    Dialog.dialogs = []
    runner = Runner()
    runner.deferred = True
    lease = NS(identity_id="A", is_current=lambda: True)
    window = NS(service=object(), plugin_manager=object(), runner=runner,
                toast=lambda _: pytest.fail("No result without user consent"))
    page = OrganizerPageAdvice(
        "private-token", 1, 1, 1, (), 0, 0, False, "partial",
        dry_run_state="confirmed", dry_run_entries=(
            OrganizerDryRunAdvice("rename", "old.epub", "new.epub", "review_only"),
        ),
    )
    ui = NativeOrganizerConfirmation(window, object(), lease, Gtk=NS(Button=Widget))
    ui.observe(page)

    def broken(*_args, **_kwargs):
        raise RuntimeError("PRIVATE_DIALOG_BUILD_FAILURE")

    with monkeypatch.context() as patch:
        patch.setattr(Dialog, phase, classmethod(broken) if phase == "new" else broken)
        assert ui.open(NS(MessageDialog=Dialog)) is False
        assert ui._dialog is None and ui._page is page and ui._used is None
        assert ui.button.sensitive and not runner.queue
        if Dialog.dialogs:
            assert "response" not in Dialog.dialogs[-1].signals

    assert ui.open(NS(MessageDialog=Dialog)) is True
    Dialog.dialogs[-1].respond("cancel")
    assert ui._used is None and ui.button.sensitive and not runner.queue


@pytest.mark.parametrize("malformed", [
    "none_entries", "list_entries", "text_entries", "bad_entry",
    "none_current", "newline_current", "del_proposed",
    "oversized_proposed", "empty_proposed", "bidi_current",
    "c1_proposed", "zero_width_current", "unicode_line_proposed",
])
def test_native_review_refuses_malformed_or_unsafe_plugin_advice_without_dialog(
    malformed,
):
    Dialog.dialogs = []
    runner = Runner()
    runner.deferred = True
    lease = NS(identity_id="A", is_current=lambda: True)
    window = NS(service=object(), plugin_manager=object(), runner=runner,
                toast=lambda _: pytest.fail("Malformed advice cannot start work"))
    advice = OrganizerDryRunAdvice("rename", "old.epub", "new.epub", "review_only")
    page = OrganizerPageAdvice(
        "private-token", 1, 1, 1, (), 0, 0, False, "partial",
        dry_run_state="confirmed", dry_run_entries=(advice,),
    )
    changes = {
        "none_entries": None,
        "list_entries": [advice],
        "text_entries": "rename",
        "bad_entry": (object(),),
        "none_current": (replace(advice, current=None),),
        "newline_current": (replace(advice, current="old\nfile.epub"),),
        "del_proposed": (replace(advice, proposed="new\x7ffile.epub"),),
        "oversized_proposed": (replace(advice, proposed="x" * 4097),),
        "empty_proposed": (replace(advice, proposed=""),),
        "bidi_current": (replace(advice, current="old\u202efile.epub"),),
        "c1_proposed": (replace(advice, proposed="new\x85file.epub"),),
        "zero_width_current": (replace(advice, current="old\u200dfile.epub"),),
        "unicode_line_proposed": (replace(advice, proposed="new\u2028file.epub"),),
    }
    ui = NativeOrganizerConfirmation(window, object(), lease, Gtk=NS(Button=Widget))
    ui.observe(replace(page, dry_run_entries=changes[malformed]))
    assert not ui.button.visible and not ui.button.sensitive
    assert not ui.open(NS(MessageDialog=Dialog))
    assert not runner.queue and not Dialog.dialogs

    # A later valid Core snapshot may re-enable the same widget.
    ui.observe(page)
    assert ui.button.visible and ui.button.sensitive
    assert ui.open(NS(MessageDialog=Dialog))
    Dialog.dialogs[-1].respond("cancel")
    assert not runner.queue and ui._used is None


@pytest.mark.parametrize("closure", ["normal", "raises", "late_apply"])
def test_native_dialog_is_revoked_and_closed_on_organizer_disposal(
    monkeypatch, closure,
):
    Dialog.dialogs = []
    runner = Runner()
    runner.deferred = True
    calls, closed = [], []
    window = NS(service=object(), plugin_manager=object(), runner=runner,
                toast=lambda _: pytest.fail("Disposed view cannot receive a result"))
    lease = NS(identity_id="A", is_current=lambda: True)
    advice = OrganizerDryRunAdvice("rename", "old.epub", "new.epub", "review_only")
    page = OrganizerPageAdvice(
        "private-token", 1, 1, 1, (), 0, 0, False, "partial",
        dry_run_state="confirmed", dry_run_entries=(advice,),
    )
    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        pytest.fail("Revoked dialog must not admit Core work")
    monkeypatch.setattr(execution, "admit_native_reviewed_rename", forbidden)
    def close(dialog):
        closed.append(dialog)
        if closure == "raises":
            raise RuntimeError("native close failed")
        if closure == "normal":
            dialog.respond("cancel")
    monkeypatch.setattr(Dialog, "close", close, raising=False)

    ui = NativeOrganizerConfirmation(window, object(), lease, Gtk=NS(Button=Widget))
    ui.observe(page)
    assert ui.open(NS(MessageDialog=Dialog))
    dialog, = Dialog.dialogs
    ui.dispose()
    assert closed == [dialog]
    assert ui._dialog is None and ui._page is None and ui._used is None
    assert not ui.button.visible and not ui.button.sensitive
    if closure == "late_apply":
        dialog.respond("apply")
    assert not runner.queue and not calls


@pytest.mark.parametrize("phase", ["observe", "open", "response", "worker"])
def test_native_confirmation_handles_activation_lease_exception_without_core_work(
    monkeypatch, phase,
):
    Dialog.dialogs = []
    runner = Runner()
    runner.deferred = True
    failure = [phase == "observe"]
    messages = []

    def current():
        if failure[0]:
            raise RuntimeError("activation lease already disposed")
        return True

    def forbidden(*_args, **_kwargs):
        pytest.fail("Revoked native review cannot admit Core execution")

    monkeypatch.setattr(execution, "admit_native_reviewed_rename", forbidden)
    lease = NS(identity_id="A", is_current=current)
    window = NS(service=object(), plugin_manager=object(),
                runner=runner, toast=messages.append)
    entry = OrganizerDryRunAdvice("rename", "old.epub", "new.epub", "review_only")
    page = OrganizerPageAdvice(
        "private-token", 1, 1, 1, (), 0, 0, False, "partial",
        dry_run_state="confirmed", dry_run_entries=(entry,),
    )
    ui = NativeOrganizerConfirmation(window, object(), lease, Gtk=NS(Button=Widget))
    ui.observe(page)
    if phase == "observe":
        assert not ui.button.visible and not ui.open(NS(MessageDialog=Dialog))
    elif phase == "open":
        failure[0] = True
        assert not ui.open(NS(MessageDialog=Dialog))
    else:
        assert ui.open(NS(MessageDialog=Dialog))
        dialog, = Dialog.dialogs
        if phase == "response":
            failure[0] = True
        dialog.respond("apply")
        if phase == "worker":
            assert len(runner.queue) == 1
            failure[0] = True
            runner.run()
    assert not runner.queue and not messages
    assert ui._used is (page if phase == "worker" else None)
    assert not ui._lease_current()


@pytest.mark.parametrize("refresh", ["revoked", "different", "invalid", "close_raises"])
def test_refresh_closes_superseded_native_dialog_and_old_apply_is_inert(monkeypatch, refresh):
    Dialog.dialogs = []
    runner = Runner()
    runner.deferred = True
    closed, calls = [], []
    lease = NS(identity_id="A", is_current=lambda: True)
    window = NS(service=object(), plugin_manager=object(), runner=runner,
                toast=lambda _message: pytest.fail("Stale native review cannot notify"))
    monkeypatch.setattr(
        execution, "admit_native_reviewed_rename",
        lambda *_a, **_k: calls.append("admitted"),
    )
    entry = OrganizerDryRunAdvice("rename", "old.epub", "new.epub", "review_only")
    page = OrganizerPageAdvice(
        "private-token", 1, 1, 1, (), 0, 0, False, "partial",
        dry_run_state="confirmed", dry_run_entries=(entry,),
    )
    ui = NativeOrganizerConfirmation(window, object(), lease, Gtk=NS(Button=Widget))
    ui.observe(page)
    assert ui.open(NS(MessageDialog=Dialog))
    old, = Dialog.dialogs

    def close(dialog):
        closed.append(dialog)
        if refresh == "close_raises":
            raise RuntimeError("GTK close failed")
        dialog.respond("cancel")
    monkeypatch.setattr(Dialog, "close", close, raising=False)

    if refresh == "revoked":
        next_page = None
    elif refresh == "invalid":
        next_page = replace(page, dry_run_entries=())
    else:
        next_page = replace(page, dry_run_entries=(
            replace(entry, proposed="newer.epub"),))
    ui.observe(next_page)
    assert closed == [old] and ui._dialog is None
    assert ui._page is (next_page if refresh in ("different", "close_raises") else None)
    old.respond("apply")
    assert not calls and not runner.queue
    if refresh in ("different", "close_raises"):
        assert ui.button.visible and ui.button.sensitive
        assert ui.open(NS(MessageDialog=Dialog))
        assert len(Dialog.dialogs) == 2
        Dialog.dialogs[-1].respond("cancel")
    else:
        assert not ui.button.visible and not ui.button.sensitive


def test_repeat_exact_page_does_not_close_presented_dialog(monkeypatch):
    Dialog.dialogs = []
    runner = Runner()
    runner.deferred = True
    entry = OrganizerDryRunAdvice("rename", "old.epub", "new.epub", "review_only")
    page = OrganizerPageAdvice(
        "private-token", 1, 1, 1, (), 0, 0, False, "partial",
        dry_run_state="confirmed", dry_run_entries=(entry,),
    )
    window = NS(service=object(), plugin_manager=object(), runner=runner,
                toast=lambda _message: None)
    ui = NativeOrganizerConfirmation(
        window, object(), NS(identity_id="A", is_current=lambda: True),
        Gtk=NS(Button=Widget),
    )
    ui.observe(page)
    assert ui.open(NS(MessageDialog=Dialog))
    original = Dialog.dialogs[-1]
    monkeypatch.setattr(Dialog, "close", lambda _dialog: pytest.fail(
        "same exact page must not discard active confirmation"), raising=False)
    ui.observe(page)
    assert ui._dialog is original and not ui.button.sensitive
    original.respond("cancel")
    assert not runner.queue


@pytest.mark.parametrize("dispose_with_dialog", [False, True])
def test_disposed_native_widget_cannot_rearm_from_late_observation(
    monkeypatch, dispose_with_dialog,
):
    Dialog.dialogs = []
    runner = Runner()
    runner.deferred = True
    lease = NS(identity_id="A", is_current=lambda: True)
    window = NS(service=object(), plugin_manager=object(), runner=runner,
                toast=lambda _: pytest.fail("Disposed widget cannot report"))
    page = OrganizerPageAdvice(
        "private-token", 1, 1, 1, (), 0, 0, False, "partial",
        dry_run_state="confirmed",
        dry_run_entries=(OrganizerDryRunAdvice(
            "rename", "old.epub", "new.epub", "review_only"),),
    )
    monkeypatch.setattr(
        execution, "admit_native_reviewed_rename",
        lambda *_args, **_kwargs: pytest.fail("Disposed widget cannot admit Core"),
    )
    ui = NativeOrganizerConfirmation(window, object(), lease, Gtk=NS(Button=Widget))
    ui.observe(page)
    assert ui.button.visible
    if dispose_with_dialog:
        assert ui.open(NS(MessageDialog=Dialog))
        prior = Dialog.dialogs[-1]
    ui.dispose()
    assert ui._disposed and not ui._lease_current()
    # A queued observer can still deliver the very same consent DTO.
    ui.observe(page)
    ui.observe(replace(page, token="later-review"))
    assert not ui.button.visible and not ui.button.sensitive
    assert not ui.open(NS(MessageDialog=Dialog))
    if dispose_with_dialog:
        prior.respond("apply")
    assert not runner.queue and ui._page is None and ui._dialog is None


@pytest.mark.parametrize("invalid_owner", [
    "A\nFORGED", "A\x85FORGED", "A\u202eFORGED",
    "A\u200dFORGED", "A" * 1025, None, 7,
])
def test_native_confirmation_never_presents_unsafe_owner_identity(
    monkeypatch, invalid_owner,
):
    Dialog.dialogs = []
    runner = Runner()
    runner.deferred = True
    window = NS(service=object(), plugin_manager=object(), runner=runner,
                toast=lambda _: pytest.fail("Unrenderable owner cannot report"))
    lease = NS(identity_id=invalid_owner, is_current=lambda: True)
    page = OrganizerPageAdvice(
        "private-token", 1, 1, 1, (), 0, 0, False, "partial",
        dry_run_state="confirmed",
        dry_run_entries=(OrganizerDryRunAdvice(
            "rename", "old.epub", "new.epub", "review_only"),),
    )
    monkeypatch.setattr(
        execution, "admit_native_reviewed_rename",
        lambda *_a, **_k: pytest.fail("Unsafe owner cannot admit Core"),
    )
    ui = NativeOrganizerConfirmation(window, object(), lease, Gtk=NS(Button=Widget))
    ui.observe(page)
    assert not ui.button.visible and not ui.button.sensitive
    assert not ui.open(NS(MessageDialog=Dialog))
    assert not Dialog.dialogs and not runner.queue


@pytest.mark.parametrize("phase", ["before_open", "after_open"])
def test_native_confirmed_rename_cannot_switch_identity_after_mount(
    monkeypatch, phase,
):
    Dialog.dialogs = []
    runner = Runner()
    runner.deferred = True
    lease = NS(identity_id="A", is_current=lambda: True)
    window = NS(service=object(), plugin_manager=object(), runner=runner,
                toast=lambda _: pytest.fail("Changed owner cannot report"))
    page = OrganizerPageAdvice(
        "private-token", 1, 1, 1, (), 0, 0, False, "partial",
        dry_run_state="confirmed",
        dry_run_entries=(OrganizerDryRunAdvice(
            "rename", "old.epub", "new.epub", "review_only"),),
    )
    monkeypatch.setattr(
        execution, "admit_native_reviewed_rename",
        lambda *_a, **_k: pytest.fail("Changed owner cannot admit Core"),
    )
    ui = NativeOrganizerConfirmation(window, object(), lease, Gtk=NS(Button=Widget))
    ui.observe(page)
    assert ui.button.visible
    if phase == "after_open":
        assert ui.open(NS(MessageDialog=Dialog))
        dialog = Dialog.dialogs[-1]
        assert "Profil : A\n" in dialog.body
    lease.identity_id = "B"
    assert not ui._lease_current()
    if phase == "before_open":
        assert not ui.open(NS(MessageDialog=Dialog))
    else:
        dialog.respond("apply")
    ui.observe(page)
    assert not ui.button.visible and not ui.button.sensitive
    assert not runner.queue


@pytest.mark.parametrize("stale_response", ["apply", "cancel"])
def test_old_native_dialog_response_cannot_clear_new_modal_after_refresh(
    monkeypatch, stale_response,
):
    Dialog.dialogs = []
    runner = Runner()
    runner.deferred = True
    lease = NS(identity_id="A", is_current=lambda: True)
    window = NS(service=object(), plugin_manager=object(), runner=runner,
                toast=lambda _: pytest.fail("Stale dialog must not deliver a result"))
    monkeypatch.setattr(
        execution, "admit_native_reviewed_rename",
        lambda *_a, **_k: pytest.fail("Stale dialog must not admit Core"),
    )
    entry = OrganizerDryRunAdvice("rename", "old.epub", "new.epub", "review_only")
    page = OrganizerPageAdvice(
        "private-token", 1, 1, 1, (), 0, 0, False, "partial",
        dry_run_state="confirmed", dry_run_entries=(entry,),
    )
    ui = NativeOrganizerConfirmation(window, object(), lease, Gtk=NS(Button=Widget))
    ui.observe(page)
    assert ui.open(NS(MessageDialog=Dialog))
    old_dialog = Dialog.dialogs[-1]
    # Native close is asynchronous: its cancellation may arrive after a new
    # review opens. Simulate that documented scheduling without GTK timing.
    closed = []
    monkeypatch.setattr(Dialog, "close", lambda self: closed.append(self), raising=False)
    next_page = replace(page, dry_run_entries=(
        replace(entry, proposed="different.epub"),))
    ui.observe(next_page)
    assert closed == [old_dialog] and ui._dialog is None
    assert ui.open(NS(MessageDialog=Dialog))
    new_dialog = Dialog.dialogs[-1]
    assert new_dialog is not old_dialog
    assert ui._dialog is new_dialog and not ui.button.sensitive
    old_dialog.respond(stale_response)
    assert ui._dialog is new_dialog and not ui.button.sensitive
    assert not runner.queue
    new_dialog.respond("cancel")
    assert ui._dialog is None and ui.button.sensitive
    assert not runner.queue


@pytest.mark.parametrize("phase", [
    "before_open", "before_response", "queued_worker", "observed_refresh",
])
def test_native_consent_witness_rejects_in_place_dto_mutation(
    monkeypatch, phase,
):
    Dialog.dialogs = []
    runner = Runner()
    runner.deferred = True
    messages = []
    window = NS(service=object(), plugin_manager=object(), runner=runner,
                toast=messages.append)
    lease = NS(identity_id="A", is_current=lambda: True)
    monkeypatch.setattr(
        execution, "admit_native_reviewed_rename",
        lambda *_a, **_k: pytest.fail("Changed review cannot admit Core"),
    )
    entry = OrganizerDryRunAdvice("rename", "old.epub", "new.epub", "review_only")
    page = OrganizerPageAdvice(
        "private-token", 1, 1, 1, (), 0, 0, False, "partial",
        dry_run_state="confirmed", dry_run_entries=(entry,),
    )
    ui = NativeOrganizerConfirmation(window, object(), lease, Gtk=NS(Button=Widget))
    ui.observe(page)
    assert ui.button.visible
    if phase == "before_open":
        object.__setattr__(entry, "proposed", "changed.epub")
        assert not ui.open(NS(MessageDialog=Dialog))
        assert not Dialog.dialogs and not runner.queue
    else:
        assert ui.open(NS(MessageDialog=Dialog))
        dialog = Dialog.dialogs[-1]
        assert "new.epub" in dialog.body
        if phase == "queued_worker":
            dialog.respond("apply")
            assert len(runner.queue) == 1
        object.__setattr__(entry, "proposed", "changed.epub")
        assert not ui._current(page)
        if phase == "observed_refresh":
            monkeypatch.setattr(Dialog, "close", lambda _self: None, raising=False)
            ui.observe(page)
        if phase != "queued_worker":
            dialog.respond("apply")
            assert not runner.queue
            if phase == "observed_refresh":
                # The freshly observed DTO can be reviewed again, but the
                # former modal cannot consume or rearm its confirmation.
                assert ui._dialog is None and ui.button.sensitive
            else:
                assert not ui.button.sensitive
        else:
            runner.run()
            assert not runner.queue
    assert not messages
