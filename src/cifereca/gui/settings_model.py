"""Headless staging and commit coordination for the settings editor."""
from __future__ import annotations

from copy import deepcopy

from ..task_queue import TaskPriority


class SettingsCommitController:
    """Keep one immutable save in flight until its UI delivery completes.

    The injected scheduler owns thread delivery and error presentation. This
    controller owns neither widgets nor persistence; the existing service
    remains responsible for atomic directory migration and rollback.
    """

    def __init__(self, save_configuration, schedule, on_error):
        self.save_configuration = save_configuration
        self.schedule = schedule
        self.on_error = on_error
        self._generation = 0
        self._active_generation = None
        self._disposed = False

    @property
    def pending(self) -> bool:
        return self._active_generation is not None

    @staticmethod
    def stage(data):
        return deepcopy(data)

    def dispose(self):
        self._disposed = True
        self._active_generation = None

    def submit(self, label, staged, on_success, *, control=None) -> bool:
        if self.pending or self._disposed:
            return False
        payload = self.stage(staged)
        self._generation += 1
        generation = self._generation
        self._active_generation = generation

        def finish():
            if self._disposed or self._active_generation != generation:
                return False
            self._active_generation = None
            return True

        def done(result):
            if finish():
                on_success(result)

        def failed(error):
            if finish():
                self.on_error(error)

        try:
            self.schedule(label, lambda: self.save_configuration(payload),
                          done, failed, control=control,
                          priority=TaskPriority.USER_ACTION, kind="settings")
        except BaseException as exc:
            failed(exc)
            return False
        return True


class SettingsRevisionState:
    """Track edits separately from the revision captured by a successful save."""

    def __init__(self, sections):
        self._current = {key: 0 for key in sections}
        self._saved = dict(self._current)

    def dirty(self, section):
        return self._current.get(section, 0) != self._saved.get(section, 0)

    def mark_dirty(self, section):
        if section == "diagnostic":
            return False
        self._current[section] = self._current.get(section, 0) + 1
        return True

    def snapshot(self):
        return dict(self._current)

    def commit(self, snapshot):
        # Edits made while the worker was running remain pending.
        for section, revision in snapshot.items():
            self._saved[section] = revision

    def reset(self, section):
        self._current[section] = self._saved.get(section, 0)
