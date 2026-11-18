"""GTK-free lifetime and scope witnesses for the opt-in ui.page contract."""
from types import SimpleNamespace as NS

from cifereca.gui import page_lifecycle_controller as lifecycle
from cifereca.plugin_api.page_lifecycle import PageLifecycleV1


def _fixture():
    calls, errors, alive = [], [], [True]
    dispatcher = lifecycle.PageLifecycleDispatcher(lambda: alive[0], errors.append)

    def mount(route, *, account=False, invalidate=None, show=None, dispose=None):
        widget = NS(cifereca_page_lifecycle_v1=PageLifecycleV1(
            invalidate or (lambda event: calls.append(("invalidate", route, event))),
            show or (lambda event: calls.append(("show", route, event))),
            dispose or (lambda: calls.append(("dispose", route, None))),
            account,
        ))
        assert dispatcher.mount(route, widget)
        return widget

    return dispatcher, mount, calls, errors, alive


def test_hidden_context_updates_are_not_projections_and_aba_invalidates_callbacks():
    d, mount, calls, errors, alive = _fixture()
    d.context_changed("alice", None)
    mount("standard")
    mount("external")
    d.show("standard")
    first = calls[-1][2]
    assert first.is_current()
    calls.clear()
    d.context_changed("bob", None)
    bob = calls[-1][2]
    assert not first.is_current()
    assert [(op, route) for op, route, _ in calls] == [
        ("invalidate", "standard"), ("invalidate", "external"), ("show", "standard")]
    calls.clear()
    d.context_changed("alice", None)
    assert not bob.is_current() and not first.is_current()
    assert [(op, route) for op, route, _ in calls] == [
        ("invalidate", "standard"), ("invalidate", "external"), ("show", "standard")]
    calls.clear()
    d.show("external")
    d.show("external")
    assert [(op, route) for op, route, _ in calls] == [("show", "external")]
    assert calls[0][2].identity_id == "alice"
    assert calls[0][2].is_current()
    assert not errors


def test_account_change_targets_only_account_scoped_pages_and_excludes_old_identity():
    d, mount, calls, errors, alive = _fixture()
    d.context_changed("alice", ("provider", "credential-a", "a"))
    mount("identity")
    mount("account", account=True)
    d.show("identity")
    event = calls[-1][2]
    calls.clear()
    d.context_changed("alice", ("provider", "credential-a", "other"))
    assert [(op, route) for op, route, _ in calls] == [("invalidate", "account")]
    assert event.is_current() and event.account_scope is None
    d.show("account")
    assert calls[-1][2].account_scope == ("provider", "credential-a", "other")
    service = NS(current_identity=NS(identity_id="bob"),
                 current_account=NS(identity_id="alice", provider_id="provider",
                                    credential_id="credential-a", unique_id="a"))
    assert lifecycle._coordinates(NS(service=service)) == ("bob", None)
    service.current_account.identity_id = "bob"
    assert lifecycle._coordinates(NS(service=service)) == ("bob", ("provider", "credential-a", "a"))


def test_deferred_projection_and_reentrant_show_are_ordered_after_all_invalidations():
    d, mount, calls, errors, alive = _fixture()
    d.context_changed("alice", None)
    mount("first")
    mount("second", invalidate=lambda event: (calls.append(("invalidate", "second", event)),
                                               d.show("second") if event.reason == "identity" else None))
    d.show("first")
    calls.clear()
    d.context_changed("bob", None, defer=True)
    assert [op for op, _, _ in calls] == ["invalidate", "invalidate"]
    d.show("second")
    assert [op for op, _, _ in calls] == ["invalidate", "invalidate"]
    d.resume()
    assert [(op, route) for op, route, _ in calls][-1] == ("show", "second")
    assert not errors


def test_mount_replacement_dispose_close_and_late_event_rejection():
    d, mount, calls, errors, alive = _fixture()
    d.context_changed("alice", None)
    mount("one")
    d.show("one")
    old = calls[-1][2]
    d.detach("one")
    assert not old.is_current(require_visible=False)
    assert calls[-1][:2] == ("dispose", "one")
    mount("one")
    d.show("one")
    current = calls[-1][2]
    assert current.is_current() and not old.is_current()
    alive[0] = False
    assert not current.is_current()
    d.dispose()
    n = len(calls)
    d.dispose()
    d.show("one")
    assert n == len(calls)


def test_hook_failure_isolated_and_old_generations_retired():
    d, mount, calls, errors, alive = _fixture()
    d.context_changed("alice", None)
    def bad(event):
        if event.identity_id == "bob":
            raise RuntimeError("private path must not be logged")
        calls.append(("invalidate", "bad", event))
    mount("bad", invalidate=bad)
    mount("good")
    d.show("bad")
    old = calls[-1][2]
    calls.clear()
    d.context_changed("bob", None)
    assert errors == ["page_context_failed"]
    assert not old.is_current()
    assert not any(op == "show" for op, _, _ in calls)
    d.show("good")
    assert calls[-1][2].identity_id == "bob"
    assert d.mount("old", NS()) is False


def test_rebuild_retires_all_previous_handles_before_dispose_callback():
    host = NS(service=NS(current_identity=NS(identity_id="alice")), _closed=False)
    old = lifecycle.reset(host)
    events, disposed = [], []
    old.mount("page", NS(cifereca_page_lifecycle_v1=PageLifecycleV1(
        lambda event: None, events.append, lambda: disposed.append(events[-1].is_current()))))
    old.show("page")
    assert events[-1].is_current()
    new = lifecycle.reset(host)
    assert old is not new and disposed == [False]
    assert not events[-1].is_current(require_visible=False)


def test_reentrant_hide_does_not_skip_invalidation_of_previous_visible_page():
    d, mount, calls, errors, alive = _fixture()
    d.context_changed("alice", None)
    mount("hidden", invalidate=lambda event: d.show("hidden") if event.reason == "identity" else None)
    mount("visible")
    d.show("visible")
    calls.clear()
    d.context_changed("bob", None)
    assert ("invalidate", "visible") in [(op, route) for op, route, _ in calls]
    assert calls[-1][:2] == ("show", "hidden")
