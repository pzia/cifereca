"""Headless task lifecycle regression coverage for #1132."""

from cifereca.gui.task_state import register_task_finish, register_task_start


def test_finish_before_start_cannot_resurrect_task():
    active, finished = {}, set()
    assert register_task_finish(active, finished, 7) == (True, "task")
    assert register_task_start(active, finished, 7, 1, "Sync", "sync") is False
    assert active == {}


def test_duplicate_start_and_finish_are_idempotent():
    active, finished = {}, set()
    assert register_task_start(active, finished, 1, 2, "Sync", "sync")
    assert not register_task_start(active, finished, 1, 2, "Sync", "sync")
    assert register_task_finish(active, finished, 1) == (True, "sync")
    assert register_task_finish(active, finished, 1) == (False, "task")
    assert active == {}


def test_finishing_one_task_keeps_other_active():
    active, finished = {}, set()
    assert register_task_start(active, finished, 1, 2, "A", "sync")
    assert register_task_start(active, finished, 2, 1, "B", "download")
    assert register_task_finish(active, finished, 1) == (True, "sync")
    assert active == {2: (1, "B", "download")}


def test_finished_history_remains_bounded():
    active, finished = {}, set()
    for task_id in range(110):
        assert register_task_finish(active, finished, task_id)[0]
    assert len(finished) <= 100
    assert 109 in finished
