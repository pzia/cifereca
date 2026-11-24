"""#2385: public C catalogue links must resolve only through source-fenced Core."""
import sqlite3
from types import SimpleNamespace as NS

from cifereca.core.notice_c_cover_index import canonical_cover_document_ref
from cifereca.core.notice_c_web_choices import PublicWebChoice, PublicWebChoices
from cifereca.gui import notice_c_public_catalogue_web_actions as actions


def test_web_menu_reads_exact_c_and_revalidates_an_opaque_choice_on_click(monkeypatch):
    document = canonical_cover_document_ref("edition:test")
    storage = object()
    window = NS(service=NS(storage=storage), _closed=False)
    active, opened, reads, resolves, pending = [True], [], [], [], []
    choice = PublicWebChoice("a" * 64, "provider", "record:1",
                             "edition:test", 20, "provider — record:1")
    menu = PublicWebChoices(document, 4, (choice,))

    class Widget:
        def __init__(self, **kwargs):
            self.label = kwargs.get("label", "")
            self.children = []
            self.handlers = {}
        def append(self, child):
            self.children.append(child)
        def remove(self, child):
            self.children.remove(child)
        def get_first_child(self):
            return self.children[0] if self.children else None
        def set_xalign(self, value):
            pass
        def set_text(self, value):
            self.label = value
        def connect(self, name, callback):
            self.handlers[name] = callback

    class Gtk:
        Box = Label = Button = Widget
        class Orientation:
            VERTICAL = 1

    class ImmediateThread:
        def __init__(self, *, target, **kwargs):
            self.target = target
        def start(self):
            self.target()

    def read(db, doc):
        reads.append((db, doc))
        return menu

    def resolve(db, doc, *, expected_epoch, choice_id):
        resolves.append((db, doc, expected_epoch, choice_id))
        return "https://example.org/qualified-record"

    monkeypatch.setattr(actions, "Thread", ImmediateThread)
    monkeypatch.setattr(actions, "read_public_web_choices", read)
    monkeypatch.setattr(actions, "resolve_public_web_choice", resolve)
    monkeypatch.setattr(actions, "_open_public_url", lambda url: opened.append(url) or True)
    glib = NS(idle_add=lambda fn, *args: pending.append((fn, args)))
    content_box = Widget()
    rows = (NS(document=document, title="Livre"),
            NS(document=document, title="Même C, autre fournisseur"))
    actions.append_public_catalogue_web_actions(
        window, content_box, rows, Gtk=Gtk, GLib=glib,
        current=lambda: active[0],
    )
    assert len(content_box.children) == 1
    group = content_box.children[0]
    group.children[0].handlers["clicked"](group.children[0])
    assert reads == [(storage, document)]
    fn, args = pending.pop()
    assert fn(*args) is False
    item = group.children[2].children[0]
    item.handlers["clicked"](item)
    assert resolves == [(storage, document, 4, "a" * 64)]
    fn, args = pending.pop()
    assert fn(*args) is False
    assert opened == ["https://example.org/qualified-record"]
    group.children[0].handlers["clicked"](group.children[0])
    active[0] = False
    fn, args = pending.pop()
    assert fn(*args) is False
    assert len(group.children[2].children) == 0
    assert len(opened) == 1

    # A locked Core read must never be presented as an attested zero of web links.
    active[0] = True

    def locked(*args):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(actions, "read_public_web_choices", locked)
    group.children[0].handlers["clicked"](group.children[0])
    fn, args = pending.pop()
    assert fn(*args) is False
    assert "lecture Core impossible" in group.children[1].label
    assert len(group.children[2].children) == 0

    # A response for a different canonical C cannot prove absence either.
    other = canonical_cover_document_ref("edition:other")
    monkeypatch.setattr(
        actions, "read_public_web_choices", lambda *args: PublicWebChoices(other, 4, (choice,)),
    )
    group.children[0].handlers["clicked"](group.children[0])
    fn, args = pending.pop()
    assert fn(*args) is False
    assert "réponse Core invalide" in group.children[1].label
    assert len(group.children[2].children) == 0
