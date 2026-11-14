"""Test-only stable fixtures, independent of collected test modules (#5376)."""
from __future__ import annotations

from types import SimpleNamespace as NS
from constellations_page import View


class DropDown:
    def __init__(self, selected):
        self.selected = selected
        self.callback = None
        self.notifications = 0

    def get_selected(self):
        return self.selected

    def set_selected(self, selected):
        self.selected = selected
        self._notify()

    def set_model(self, model):
        self.model = model
        # Gtk.DropDown can temporarily choose index 0 before we restore
        # the selected profile. Its notification must not load that profile.
        self.selected = 0 if model else 0xFFFFFFFF
        self._notify()

    def connect(self, callback):
        self.callback = callback

    def _notify(self):
        self.notifications += 1
        if self.callback:
            self.callback()


def view_with_profiles(old, new, selected=0):
    view = object.__new__(View)
    view.disposed = False
    view.cache = {"synthetic": object()}
    view._reloading_identities = False
    view.identities = old
    view.Gtk = NS(StringList=NS(new=lambda values: list(values)))
    view.plugin = NS(context=NS(
        broker=lambda name: NS(identities=lambda: list(new)),
    ))
    dropdown = DropDown(selected)
    view.identity = dropdown
    loads = []
    view.load = lambda *, refresh=False: loads.append((refresh, view.identity_id()))
    dropdown.connect(
        lambda: None if view._reloading_identities else view.load()
    )
    return view, dropdown, loads


