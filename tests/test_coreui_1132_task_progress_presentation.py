"""Headless regression tests for CoreUI task progress presentation (#1132)."""

from cifereca.gui.task_progress_presentation import live_task_progress_label


def test_no_live_task_does_not_resurrect_stale_progress():
    assert live_task_progress_label({}, "Terminé") is None


def test_late_progress_keeps_highest_priority_live_task():
    tasks = {3: (5, "Téléchargement", "download"), 4: (1, "Synchronisation", "sync")}
    assert live_task_progress_label(tasks, "Import terminé") == (
        "En cours · 2 opérations · Synchronisation"
    )


def test_live_progress_suffix_is_preserved():
    tasks = {4: (1, "Synchronisation", "sync")}
    assert live_task_progress_label(tasks, "Synchronisation 50 %") == (
        "En cours · Synchronisation 50 %"
    )


def test_unrelated_prefix_does_not_match_active_task():
    tasks = {4: (1, "Sync", "sync")}
    assert live_task_progress_label(tasks, "Synchronisation terminée") == (
        "En cours · Sync"
    )
