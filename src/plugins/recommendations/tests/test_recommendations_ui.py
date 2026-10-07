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
