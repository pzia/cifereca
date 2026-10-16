"""#1346: a completed Kobo worker must not be starved by GTK idle traffic."""
from __future__ import annotations

from queue import Queue

from tests.test_core_p0_981_task_coalescing import session


def test_cancelled_kobo_receipt_is_dispatched_before_idle_repaints(session):
    """Exercise the real TaskRunner class with a priority-aware GLib seam."""
    runner = session.runner
    glib = runner.submit.__globals__["GLib"]
    default_ready = Queue()
    idle_ready = Queue()
    priorities = []

    def schedule(fn, *args, priority=200):
        priorities.append((fn.__name__, priority))
        (default_ready if priority <= 0 else idle_ready).put((fn, args))

    glib.PRIORITY_DEFAULT = 0
    glib.idle_add = schedule

    worker, entered, release, calls = session.work()
    outcomes = []
    runner.submit(
        "Kobo confirmé", worker, ok=outcomes.append,
        kind="kobo-acquisition", origin="user",
    )
    assert entered.wait(3)

    # GTK main loop can stay busy with an unbounded stream of repaint and
    # polling callbacks. A final result must not sit behind that idle queue.
    for _ in range(150):
        schedule(lambda: None)
    assert default_ready.empty() and idle_ready.qsize() >= 151

    release.set()
    callback, args = default_ready.get(timeout=5)
    assert callback.__name__ == "deliver"
    assert ("deliver", 0) in priorities
    assert ("_task_started", 200) in priorities

    # The genuine worker result is delivered, not fabricated from the
    # cancellation button, despite pending lower-priority UI callbacks.
    callback(*args)
    assert calls == ["started"]
    assert outcomes == ["result"]
    assert runner._count == 0
    assert [name for name, _ in session.events] == ["finished"]


def test_legacy_headless_gtk_seam_still_delivers_without_priority_constant(session):
    """Old seam has no PRIORITY_DEFAULT: keep existing deterministic tests."""
    worker, entered, release, calls = session.work()
    outcomes = []
    session.runner.submit(
        "Kobo", worker, ok=outcomes.append, kind="kobo-acquisition",
    )
    assert entered.wait(3)
    release.set()
    session.drain_until(lambda: len(outcomes) == 1)
    assert outcomes == ["result"]
    assert session.runner._count == 0
