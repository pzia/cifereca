"""#923 — pure action states and real-view update without network/GTK side effects."""
from types import SimpleNamespace as NS
from youth_page import YouthView, youth_action_states

LABELS = (
    "Collecter cette source", "Plus de résultats",
    "Enrichir la bibliographie", "Ouvrir la fiche Bibliothèque",
)


def choices(**changes):
    params = dict(identity=False, source=False, page=False, book=False,
                  isbn=False, bibliography=False, local_detail=False, busy=False)
    params.update(changes)
    return youth_action_states(**params)


def test_all_sources_is_cache_view_not_multisource_collection():
    states = choices(identity=True)
    assert tuple(states) == LABELS
    assert all(not enabled for enabled, _ in states.values())
    assert "Choisir une source" in states[LABELS[0]][1]
    states = choices(identity=True, source=True)
    assert {label: enabled for label, (enabled, _) in states.items()} == {
        "Collecter cette source": True, "Plus de résultats": False,
        "Enrichir la bibliographie": False, "Ouvrir la fiche Bibliothèque": False,
    }


def test_pagination_is_scoped_to_cursor_and_busy_state():
    assert choices(identity=True, source=True, page=True)[LABELS[1]][0]
    assert not choices(identity=True, source=True, page=True, busy=True)[LABELS[1]][0]
    assert not choices(source=True, page=True)[LABELS[1]][0]


def test_bibliography_needs_selection_isbn_and_service():
    ready = dict(identity=True, source=True, book=True, isbn=True, bibliography=True)
    assert choices(**ready)[LABELS[2]][0]
    for removed in ("identity", "book", "isbn", "bibliography"):
        assert not choices(**(ready | {removed: False}))[LABELS[2]][0]
    assert not choices(identity=True, book=True, isbn=True, local_detail=False)[LABELS[3]][0]
    assert choices(identity=True, book=True, local_detail=True)[LABELS[3]][0]
    assert "locale" in choices(identity=True, book=True, local_detail=True)[LABELS[3]][1]


class Button:
    def __init__(self):
        self.sensitive = None
        self.tooltip = None

    def set_sensitive(self, value):
        self.sensitive = value

    def set_tooltip_text(self, text):
        self.tooltip = text


class Label:
    def __init__(self):
        self.label = None

    def set_label(self, text):
        self.label = text


def test_real_view_updates_without_network_and_without_selected_source():
    view = object.__new__(YouthView)
    view._action_buttons = {label: Button() for label in LABELS}
    view.actions_hint = Label()
    view.rows = {}
    view.selected_book = None
    view.next_cursor = ""
    view.next_source_plugin = ""
    view._remote_running_token = None
    view.selected_source = lambda: None
    view.selected_source_plugin = lambda: ""
    view.identity_id = lambda: "alice"
    view.host = NS(open_book_detail=lambda *_args, **_kwargs: None)
    calls = []
    view.plugin = NS(context=NS(services=lambda *_args, **_kwargs: calls.append("called") or ()))
    view.update_action_availability()
    assert all(not button.sensitive for button in view._action_buttons.values())
    assert all(len(button.tooltip) >= 12 for button in view._action_buttons.values())
    assert "cache" in view.actions_hint.label
    assert calls == []

    view.selected_source = lambda: NS(plugin_id="youth.source.milan",
                                     service=NS(browse=lambda **_kwargs: ()))
    view.selected_source_plugin = lambda: "youth.source.milan"
    view.update_action_availability()
    assert view._action_buttons[LABELS[0]].sensitive is True
    assert all(view._action_buttons[label].sensitive is False for label in LABELS[1:])
    assert calls == []


def test_selection_services_and_cursor_produce_individual_states():
    view = object.__new__(YouthView)
    view._action_buttons = {label: Button() for label in LABELS}
    view.actions_hint = Label()
    view.host = NS(open_book_detail=lambda *_args, **_kwargs: False)
    view.identity_id = lambda: "alice"
    view.selected_source_plugin = lambda: "youth.source.milan"
    view.selected_source = lambda: NS(plugin_id="youth.source.milan",
                                      service=NS(search=lambda _q, **_kw: ()))
    view.next_cursor = "2"
    view.next_source_plugin = "youth.source.milan"
    view._remote_running_token = None
    book = NS(source_id="milan", record_id="9782408060886",
              identifiers={"isbn": "9782408060886"})
    view.rows = {"milan:9782408060886": book}
    view.selected_book = book
    calls = []
    view.plugin = NS(context=NS(services=lambda *_args, **_kwargs: (
        calls.append("lookup") or (NS(service=object()),)
    )))
    view.update_action_availability()
    assert all(button.sensitive for button in view._action_buttons.values())
    assert calls == ["lookup"]
    view.next_source_plugin = "youth.source.other"
    view.update_action_availability()
    assert view._action_buttons[LABELS[1]].sensitive is False
    view.rows = {}  # changed filter/cache; old book is no longer displayed
    view.update_action_availability()
    assert view._action_buttons[LABELS[2]].sensitive is False
    assert view._action_buttons[LABELS[3]].sensitive is False
