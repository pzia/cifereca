"""#2385: interactive GTK public C navigation over preloaded immutable pages."""
from cifereca.core.notice_c_catalogue_views import project_public_catalogue_view
from cifereca.gui.notice_c_public_catalogue_navigator import (
    build_public_catalogue_navigator,
)
from tests.test_core_p1_2385_catalogue_views import _page


class Widget:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.children = []
        self.handlers = {}

    def append(self, child):
        self.children.append(child)

    def remove(self, child):
        self.children.remove(child)

    def get_first_child(self):
        return self.children[0] if self.children else None

    def connect(self, event, callback):
        self.handlers[event] = callback

    def click(self):
        self.handlers["clicked"](self)

    def set_xalign(self, value):
        pass

    def set_wrap(self, value):
        pass


class Gtk:
    Box = Label = Button = Widget

    class Orientation:
        VERTICAL = 1
        HORIZONTAL = 2


def test_mode_switch_and_pagination_keep_explicit_c_views():
    calls = []

    def load(mode, page):
        calls.append((mode, page))
        return project_public_catalogue_view(_page(), mode=mode)

    root = build_public_catalogue_navigator(Gtk=Gtk, load=load)
    controls, content = root.children
    assert calls == [("list", 0)]
    controls.children[4].click()
    assert calls[-1] == ("sources", 0)
    assert len(content.children[0].children) >= 3
    content.children[1].click()
    assert calls[-1] == ("sources", 1)
    controls.children[5].click()
    assert calls[-1] == ("timeline", 0)


def test_invalid_initial_mode_rejected():
    import pytest
    with pytest.raises(ValueError):
        build_public_catalogue_navigator(
            Gtk=Gtk, load=lambda *_: None, initial_mode="loans",
        )
