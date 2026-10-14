"""Injectable GTK line renderer for maintenance progress (#1492).

No PyGObject import at module import time; render callbacks only run via
the owner-supplied GTK idle scheduler.
"""
from __future__ import annotations

from .maintenance_line_progress import MaintenanceLineProgress, MaintenanceLineState


def build_maintenance_line(*, Gtk, idle_add, alive):
    status = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
    label = Gtk.Label(label="Au repos", xalign=1)
    label.set_tooltip_text("État de l’opération")
    status.append(label)
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
    spinner = Gtk.Spinner()
    progress_bar = Gtk.ProgressBar()
    progress_bar.set_show_text(False)
    spinner.set_visible(False)
    progress_bar.set_visible(False)
    row.append(spinner)
    row.append(progress_bar)
    status.append(row)

    def render(state: MaintenanceLineState) -> None:
        label.set_text(state.label)
        busy = state.phase in ("analysis", "running") and state.fraction is None
        spinner.set_visible(busy)
        if busy:
            spinner.start()
        else:
            spinner.stop()
        progress_bar.set_visible(state.fraction is not None)
        if state.fraction is not None:
            progress_bar.set_fraction(state.fraction)

    feedback = MaintenanceLineProgress(
        idle_add=idle_add, render=render, alive=alive,
    )
    return status, feedback
