from types import SimpleNamespace

from recommendations import UxRecommendationsView, _refresh_on_first_map


def ranked(title, score=50, provider="a"):
    return SimpleNamespace(
        item={"title": title, "author": "Auteur", "source_provider": provider},
        score=score,
        reasons=("raison",),
        sources=(provider,),
    )


def test_partial_order_keeps_existing_rows_stable():
    view = object.__new__(UxRecommendationsView)
    view._provisional_order = {}
    first = view._stable_partial([ranked("A"), ranked("B")])
    assert [row.item["title"] for row in first] == ["A", "B"]

    # A later batch may score B before A; provisional UX must not jump.
    second = view._stable_partial([ranked("B", 90), ranked("A", 40), ranked("C", 80)])
    assert [row.item["title"] for row in second] == ["A", "B", "C"]


def test_provider_ids_are_never_user_facing_when_label_is_known():
    view = object.__new__(UxRecommendationsView)
    view._provider_labels = {"": "Tous les catalogues", "a": "Catalogue A"}
    assert view._display_source("a") == "Catalogue A"
    assert view._display_source("unknown") == "unknown"


class _FakeWidget:
    def __init__(self):
        self.callback = None
        self.mapped = False

    def connect(self, signal, callback):
        assert signal == "map"
        self.callback = callback
        return 1

    def get_mapped(self):
        return self.mapped

    def emit_map(self):
        assert self.callback is not None
        self.mapped = True
        self.callback(self)


class _FakeGLib:
    def __init__(self):
        self.calls = []

    def idle_add(self, callback):
        self.calls.append(callback)
        return len(self.calls)


class _FakeView:
    def refresh(self):
        raise AssertionError("refresh must be scheduled, not executed synchronously")


def test_initial_refresh_waits_for_first_map_and_is_scheduled_once():
    widget = _FakeWidget()
    glib = _FakeGLib()
    view = _FakeView()

    _refresh_on_first_map(view, widget, glib)
    assert glib.calls == []

    widget.emit_map()
    assert glib.calls == [view.refresh]

    widget.emit_map()
    assert glib.calls == [view.refresh]


def test_already_mapped_widget_schedules_initial_refresh_immediately():
    widget = _FakeWidget()
    widget.mapped = True
    glib = _FakeGLib()
    view = _FakeView()

    _refresh_on_first_map(view, widget, glib)
    assert glib.calls == [view.refresh]


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


def _identity_reload_view(rows, *, active_identity):
    view = object.__new__(UxRecommendationsView)
    dropdown = _NoisyIdentityDropDown()
    model = _NoisyIdentityModel(dropdown, ("Default",))
    view._identity = dropdown
    view._identity_model = model
    view._identity_ids = ["default"]
    view._reloading_identities = False
    view._active_identity_id = active_identity
    view.host = SimpleNamespace(identity_overview=lambda: list(rows))
    view.plugin = SimpleNamespace(context=None)
    refreshes = []
    view.refresh = lambda: refreshes.append(view.selected_identity_id())
    dropdown.callback = view._identity_changed
    return view, dropdown, refreshes


def test_identity_overview_reload_suppresses_transient_selection_refreshes():
    rows = [
        {"identity_id": "default", "label": "Default", "selected": True},
        {"identity_id": "other", "label": "Other", "selected": False},
    ]
    view, dropdown, refreshes = _identity_reload_view(rows, active_identity="default")

    view._reload_identities(refresh_after=True)

    # The fake GTK model emits several transient selection notifications while
    # being rebuilt, but Recommendations must start only one fresh computation.
    assert dropdown.notifications >= 3
    assert refreshes == ["default"]
    assert view._identity_ids == ["default", "other"]


def test_identity_overview_before_first_map_does_not_duplicate_initial_refresh():
    rows = [
        {"identity_id": "default", "label": "Default", "selected": True},
        {"identity_id": "other", "label": "Other", "selected": False},
    ]
    view, _dropdown, refreshes = _identity_reload_view(rows, active_identity=None)

    view._reload_identities(refresh_after=True)

    assert refreshes == []
