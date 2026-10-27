"""#1905: Numilog startup indexing refreshes only the affected GTK library."""
from __future__ import annotations

from types import SimpleNamespace as NS
import pytest

from cifereca.gui import numilog_materialization_startup as startup
from cifereca.plugin_api import SyncResult


def _scenario(monkeypatch, *, identity="alice", outcomes=None):
    events, tasks, errors = [], [], []
    outcomes = outcomes or {"alice": (True, 1)}
    broker = NS(
        synchronize_scheduled_numilog_materialization=lambda owner, trigger, **kw: (
            events.append(("worker", owner, trigger))
            or SyncResult(
                ok=outcomes[owner][0], changed=outcomes[owner][1],
                facts={"status": "complete" if outcomes[owner][0] else "partial"},
            )
        ),
    )
    runner = NS(
        submit=lambda *args, **kwargs: tasks.append((args, kwargs)),
        present_error=lambda exc: errors.append(str(exc)),
    )
    window = NS(
        _closed=False,
        service=NS(current_identity=NS(identity_id=identity)),
        runner=runner,
        toast=lambda message: events.append(("toast", message)),
        refresh_library=lambda scan: events.append(("refresh", scan)),
    )
    monkeypatch.setattr(startup, "_broker", lambda _window: broker)
    monkeypatch.setattr(
        startup, "_configured_identity_ids", lambda _window: tuple(outcomes),
    )
    monkeypatch.setattr(
        startup, "eligible_source_ids",
        lambda _broker, owner: (f"source:{owner}",),
    )
    assert startup.dispatch_after_opening(window)
    assert len(tasks) == 1
    args, opts = tasks[0]
    assert opts["kind"] == "source-materialize"
    assert opts["origin"] == "automatic"
    assert opts["with_progress"] is True
    return window, broker, events, tasks[0], errors


def test_successful_auto_backup_refreshes_only_current_modified_library(monkeypatch):
    window, broker, events, task, errors = _scenario(monkeypatch)
    args, opts = task
    assert events == []  # no index, library refresh or provider at render
    args[1](lambda _message: None)
    assert events == [("worker", "alice", "startup")]
    args[2](None)  # TaskRunner GTK success delivery
    assert events == [("worker", "alice", "startup"), ("refresh", False)]
    assert errors == []


@pytest.mark.parametrize("new_identity", ["bob", None])
def test_identity_change_while_backup_running_does_not_refresh_other_library(
    monkeypatch, new_identity,
):
    window, broker, events, task, errors = _scenario(monkeypatch)
    args, opts = task
    args[1]()
    window.service.current_identity = (
        NS(identity_id=new_identity) if new_identity else None
    )
    args[2](None)
    assert events == [("worker", "alice", "startup")]
    assert errors == []


def test_backup_changed_second_identity_refreshes_exact_selected_owner(monkeypatch):
    window, broker, events, task, errors = _scenario(
        monkeypatch, identity="bob",
        outcomes={"alice": (True, 1), "bob": (True, 2)},
    )
    args, opts = task
    args[1]()
    args[2](None)
    assert events == [
        ("worker", "alice", "startup"), ("worker", "bob", "startup"),
        ("refresh", False),
    ]


def test_zero_downloads_never_launch_library_refresh(monkeypatch):
    window, broker, events, task, errors = _scenario(
        monkeypatch, outcomes={"alice": (True, 0)},
    )
    args, opts = task
    args[1]()
    args[2](None)
    assert events == [("worker", "alice", "startup")]
    assert errors == []


def test_partial_success_refreshes_books_and_notifies_without_a_modal(
    monkeypatch,
):
    window, broker, events, task, errors = _scenario(
        monkeypatch, outcomes={"alice": (False, 1)},
    )
    args, opts = task
    with pytest.raises(RuntimeError, match="Sauvegarde initiale Numilog incomplète"):
        args[1]()
    args[3](RuntimeError("https://private.invalid?auth=SIGNED_TOKEN"))
    assert events == [("worker", "alice", "startup"), ("refresh", False),
                      ("toast", "Sauvegarde Numilog à vérifier ; consultez Synchronisation.")]
    assert errors == []
    assert "SIGNED_TOKEN" not in repr(events) + repr(errors)


@pytest.mark.parametrize("invalidate", ["closed", "broker_replaced", "cancelled"])
def test_stale_or_cancelled_window_does_not_refresh_or_echo_private_errors(
    monkeypatch, invalidate,
):
    from cifereca.plugin_api import Cancelled
    window, broker, events, task, errors = _scenario(monkeypatch)
    args, opts = task
    args[1]()
    if invalidate == "closed":
        window._closed = True
    elif invalidate == "broker_replaced":
        monkeypatch.setattr(startup, "_broker", lambda _window: object())
    args[3](
        Cancelled("PRIVATE") if invalidate == "cancelled"
        else RuntimeError("https://private.invalid?token=SECRET")
    )
    if invalidate == "cancelled":
        assert events == [("worker", "alice", "startup"), ("refresh", False)]
    else:
        assert events == [("worker", "alice", "startup")]
    assert errors == []


@pytest.mark.parametrize("failure", ["incomplete", "exception"])
def test_failed_auto_backup_without_download_is_nonblocking(monkeypatch, failure):
    window, broker, events, task, errors = _scenario(
        monkeypatch, outcomes={"alice": (False, 0)},
    )
    if failure == "exception":
        def broken(*_a, **_k):
            raise RuntimeError("synthetic startup failure")
        broker.synchronize_scheduled_numilog_materialization = broken
    args, opts = task
    with pytest.raises(RuntimeError) as raised:
        args[1]()
    args[3](raised.value)
    assert errors == []
    assert ("refresh", False) not in events
    assert events[-1] == ("toast", "Sauvegarde Numilog à vérifier ; consultez Synchronisation.")


def test_host_without_toast_does_not_fall_back_to_a_startup_modal(monkeypatch):
    window, broker, events, task, errors = _scenario(
        monkeypatch, outcomes={"alice": (False, 0)},
    )
    del window.toast
    args, _ = task
    with pytest.raises(RuntimeError) as raised:
        args[1]()
    args[3](raised.value)
    assert errors == [] and events == [("worker", "alice", "startup")]
