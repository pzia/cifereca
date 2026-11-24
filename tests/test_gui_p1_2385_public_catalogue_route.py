"""#2385: public catalogue route is navigable with installed UI plugins."""
import sqlite3
from cifereca.builtin_ui.pages import Pages


def test_public_catalogue_is_builtin_navigation_page():
    pages = Pages().pages()
    catalogue = [page for page in pages if page.route == "public_catalogue"]
    assert len(catalogue) == 1
    assert catalogue[0].title == "Catalogue public"
    assert catalogue[0].group == "usage"


def test_public_catalogue_worker_is_not_called_by_page_factory(monkeypatch):
    from cifereca.gui import notice_c_public_catalogue_page as page

    def forbidden(*args, **kwargs):
        raise AssertionError("SQLite must not run during GTK page construction")

    monkeypatch.setattr(page, "search_public_catalogue_view", forbidden)

    class Widget:
        def __init__(self, **kwargs):
            self.children = []
            self.handlers = {}
        def append(self, child):
            self.children.append(child)
        def connect(self, name, callback):
            self.handlers[name] = callback
        def set_placeholder_text(self, value):
            pass
        def set_xalign(self, value):
            pass
        def set_text(self, value):
            pass
        def set_vexpand(self, value):
            pass
        def set_child(self, child):
            self.children.append(child)

    class Gtk:
        Box = Entry = Label = Button = ScrolledWindow = Widget
        class Orientation:
            VERTICAL = 1
            HORIZONTAL = 2

    class Window:
        pass

    root = page.build_public_catalogue_page(Window(), Gtk=Gtk, GLib=object())
    assert len(root.children) == 8


def test_public_catalogue_search_reads_core_and_rejects_old_identity_epoch(monkeypatch):
    from types import SimpleNamespace as NS
    from cifereca.gui import notice_c_public_catalogue_page as page

    pending, calls = [], []

    class Widget:
        def __init__(self, **kwargs):
            self.children = []
            self.handlers = {}
            self.value = kwargs.get("label", "")
        def append(self, child):
            self.children.append(child)
        def remove(self, child):
            self.children.remove(child)
        def get_first_child(self):
            return self.children[0] if self.children else None
        def connect(self, signal, callback):
            self.handlers[signal] = callback
        def set_placeholder_text(self, value):
            pass
        def set_xalign(self, value):
            pass
        def set_wrap(self, value):
            pass
        def set_vexpand(self, value):
            pass
        def set_child(self, child):
            self.children.append(child)
        def set_text(self, value):
            self.value = value
        def get_text(self):
            return self.value

    class Gtk:
        Box = Entry = Label = Button = ScrolledWindow = Widget
        class Orientation:
            VERTICAL = 1
            HORIZONTAL = 2

    class ImmediateThread:
        def __init__(self, *, target, **kwargs):
            self.target = target
        def start(self):
            self.target()

    def search(storage, text, **kwargs):
        calls.append((storage, text, kwargs))
        if text == "Erreur":
            raise sqlite3.OperationalError("database is locked")
        return NS(rows=(), known_documents=0, known_matches=0,
                  status="budget_exceeded" if text == "Limite" else "partial",
                  has_more=False)

    monkeypatch.setattr(page, "Thread", ImmediateThread)
    monkeypatch.setattr(page, "search_public_catalogue_view", search)
    glib = NS(idle_add=lambda fn, *args: pending.append((fn, args)))
    service = NS(storage=object(), _account_context_epoch=1,
                 _source_context_epoch=2)
    window = NS(service=service, _closed=False)
    root = page.build_public_catalogue_page(window, Gtk=Gtk, GLib=glib)
    entry, _, status, scroller, age_filters, field_filters, scope_filters, search_button = root.children
    entry.value = "Astronomie"
    search_button.handlers["clicked"](search_button)
    assert calls[0][1] == "Astronomie"
    assert calls[0][2]["mode"] == "list"
    assert calls[0][2]["field"] == "all"
    assert calls[0][2]["scope"] == "all"
    assert len(pending) == 1
    service._account_context_epoch = 3
    # A queued response from the previous account must not paint the new one.
    fn, args = pending.pop()
    assert fn(*args) is False
    assert status.value == "Recherche dans le catalogue public…"
    entry.value = "Roman"
    search_button.handlers["clicked"](search_button)
    fn, args = pending.pop()
    assert fn(*args) is False
    assert "0 notice(s) C" in status.value
    assert "Aucune notice" in scroller.children[0].children[0].value
    assert "absence dans le catalogue non démontrée" in scroller.children[0].children[0].value
    entry.value = "Limite"
    search_button.handlers["clicked"](search_button)
    fn, args = pending.pop()
    assert fn(*args) is False
    assert "budget de lecture atteint" in status.value
    assert "autres résultats possibles" in scroller.children[0].children[0].value
    field_filters.children[1].handlers["clicked"](field_filters.children[1])
    fn, args = pending.pop()
    fn(*args)
    assert calls[-1][2]["field"] == "title"
    scope_filters.children[1].handlers["clicked"](scope_filters.children[1])
    fn, args = pending.pop()
    fn(*args)
    assert calls[-1][2]["scope"] == "digital"
    assert calls[-1][2]["page"] == 0
    age_entry = age_filters.children[0]
    age_entry.value = "9"
    search_button.handlers["clicked"](search_button)
    fn, args = pending.pop()
    fn(*args)
    assert calls[-1][2]["age"] == 9
    assert calls[-1][2]["age_kind"] is None
    age_filters.children[2].handlers["clicked"](age_filters.children[2])
    fn, args = pending.pop()
    fn(*args)
    assert calls[-1][2]["age_kind"] == "reading"
    previous = len(calls)
    age_entry.value = "99"
    search_button.handlers["clicked"](search_button)
    assert len(calls) == previous
    assert "Âge invalide" in status.value
    age_entry.value = ""
    search_button.handlers["clicked"](search_button)
    fn, args = pending.pop()
    fn(*args)
    assert calls[-1][2]["age"] is None
    assert calls[-1][2]["age_kind"] is None

    entry.value = "Erreur"
    search_button.handlers["clicked"](search_button)
    fn, args = pending.pop()
    assert fn(*args) is False
    assert "Catalogue indisponible" in status.value
    assert not scroller.children[0].children
