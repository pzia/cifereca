"""Headless one-shot idle callback contracts for #1132."""

from types import SimpleNamespace

from cifereca.gui.startup_lifecycle import once_while_open


def test_open_window_dispatches_once_per_idle_invocation():
    window = SimpleNamespace(_closed=False)
    calls = []
    idle = once_while_open(window, lambda: calls.append("called"))
    assert idle() is False
    assert calls == ["called"]


def test_closed_before_dispatch_never_invokes_operation():
    window = SimpleNamespace(_closed=False)
    calls = []
    idle = once_while_open(window, lambda: calls.append("called"))
    window._closed = True
    assert idle() is False
    assert calls == []


def test_missing_closed_flag_is_treated_as_open():
    calls = []
    assert once_while_open(SimpleNamespace(), lambda: calls.append(1))() is False
    assert calls == [1]


def test_open_operation_that_closes_window_remains_one_shot():
    window = SimpleNamespace(_closed=False)
    def action():
        window._closed = True
    idle = once_while_open(window, action)
    assert idle() is False
    assert window._closed
