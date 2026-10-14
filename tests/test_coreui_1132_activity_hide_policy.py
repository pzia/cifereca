"""Activity feedback collapse contract (#1132)."""

from cifereca.gui.activity_hide_policy import can_reduce_activity


def test_completed_idle_view_can_reduce():
    assert can_reduce_activity(closed=False, active_count=0, pending_error=False, has_view=True)


def test_live_tasks_never_reduce():
    assert not can_reduce_activity(closed=False, active_count=2, pending_error=False, has_view=True)


def test_pending_error_requires_acknowledgement():
    assert not can_reduce_activity(closed=False, active_count=0, pending_error=True, has_view=True)


def test_closed_or_unbuilt_view_is_not_touched():
    assert not can_reduce_activity(closed=True, active_count=0, pending_error=False, has_view=True)
    assert not can_reduce_activity(closed=False, active_count=0, pending_error=False, has_view=False)
