"""Identity selection and refresh headless fixtures; no collected tests (#5376)."""
from __future__ import annotations

from types import SimpleNamespace
from recommendations import UxRecommendationsView, _library_overview_signature


class _NoisyIdentityDropDown:
    def __init__(self):
        self.selected = 0
        self.callback = None
        self.notifications = 0
        self.sensitive = True

    def get_selected(self):
        return self.selected

    def set_selected(self, value):
        self.selected = int(value)
        self._notify()

    def set_sensitive(self, value):
        self.sensitive = bool(value)

    def _notify(self):
        self.notifications += 1
        if self.callback is not None:
            self.callback(self, None)


class _NoisyIdentityModel:
    def __init__(self, dropdown, labels=()):
        self.dropdown = dropdown
        self.items = list(labels)

    def get_n_items(self):
        return len(self.items)

    def remove(self, index):
        self.items.pop(index)
        self.dropdown._notify()

    def append(self, label):
        self.items.append(label)
        if len(self.items) == 1:
            self.dropdown.selected = 0
        self.dropdown._notify()


class _TrackingSnapshotCache:
    def __init__(self):
        self.invalidations = []

    def invalidate(self, identity_id=None):
        self.invalidations.append(identity_id)


class _CoalescingGLib:
    def __init__(self):
        self.next_id = 1
        self.sources = {}

    def timeout_add(self, _delay, callback):
        source_id = self.next_id
        self.next_id += 1
        self.sources[source_id] = callback
        return source_id

    def source_remove(self, source_id):
        self.sources.pop(source_id, None)

    def run_pending(self):
        callbacks = list(self.sources.items())
        self.sources.clear()
        for _source_id, callback in callbacks:
            callback()


def _identity_reload_view(rows, *, active_identity, busy=False):
    view = object.__new__(UxRecommendationsView)
    dropdown = _NoisyIdentityDropDown()
    model = _NoisyIdentityModel(dropdown, ("Default",))
    view._identity = dropdown
    view._identity_model = model
    view._identity_ids = ["default"]
    view._reloading_identities = False
    view._identity_library_signatures = {
        str(row.get("identity_id") or ""): _library_overview_signature(row)
        for row in rows
        if str(row.get("identity_id") or "")
    }
    view._library_snapshot_cache = _TrackingSnapshotCache()
    view._active_identity_id = active_identity
    view._busy = busy
    view._overview_refresh_source_id = 0
    view._overview_refresh_pending = False
    view.GLib = _CoalescingGLib()
    view.host = SimpleNamespace(identity_overview=lambda: list(rows))
    view.plugin = SimpleNamespace(context=None, _pending_reference=None)
    view._reference_book = None
    view._reference_books = []
    view._reload_reference_books = lambda books: None
    refreshes = []

    def refreshed():
        view._clear_overview_refresh()
        refreshes.append(view.selected_identity_id())

    view.refresh = refreshed
    dropdown.callback = view._identity_changed
    return view, dropdown, refreshes


