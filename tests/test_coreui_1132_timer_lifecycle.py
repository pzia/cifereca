"""Headless lifecycle tests for GTK timer cleanup (#1132)."""

from cifereca.gui.timer_lifecycle import cancel_source


def test_none_source_does_not_call_glib():
    calls = []
    cancel_source(None, calls.append)
    assert calls == []


def test_live_source_is_removed_exactly_once():
    calls = []
    cancel_source(42, calls.append)
    assert calls == [42]


def test_completed_source_removal_failure_is_ignored():
    def already_gone(_source):
        raise RuntimeError("source expired")
    assert cancel_source(99, already_gone) is None
