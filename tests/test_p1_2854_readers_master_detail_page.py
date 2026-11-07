"""P1 #2854: Liseuses page reuses the shared MasterDetailPage without USB I/O."""
from __future__ import annotations

import ast
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.builtin_ui.pages import Pages
from cifereca.gui import reader_page_controller
from cifereca.gui.recent_time import format_recent_timestamp
from cifereca.gui.shell_page_controller import build_standard_page
from cifereca.identities import Identity
from cifereca.storage import Storage


class FakeBox:
    def __init__(self):
        self.children = []

    def append(self, child):
        self.children.append(child)


class FakeLabel:
    def __init__(self, label="", xalign=None):
        self.label = label

    def set_label(self, text):
        self.label = text

    def set_wrap(self, *_):
        pass

    set_margin_start = set_margin_end = set_margin_bottom = set_wrap


class FakeDropDown:
    def __init__(self, labels):
        self.labels = list(labels)
        self.selected = 0
        self.callbacks = {}

    @classmethod
    def new_from_strings(cls, labels):
        return cls(labels)

    def set_model(self, model):
        self.labels = list(model)

    def set_selected(self, index):
        self.selected = index

    def get_selected(self):
        return self.selected

    def set_hexpand(self, *_):
        pass

    def set_tooltip_text(self, *_):
        pass

    def connect(self, name, callback):
        self.callbacks[name] = callback

    def trigger(self):
        self.callbacks["notify::selected"](self, None)


class FakeButton:
    def __init__(self, label=""):
        self.label = label
        self.sensitive = True
        self.callbacks = {}

    @classmethod
    def new_from_icon_name(cls, _name):
        return cls()

    def set_label(self, label):
        self.label = label

    def set_sensitive(self, value):
        self.sensitive = value

    def set_tooltip_text(self, *_):
        pass

    def connect(self, name, callback):
        self.callbacks[name] = callback


class FakeDetail:
    def __init__(self):
        self.presentation = None
        self.empty_status = None

    def show_presentation(self, presentation, **_):
        self.presentation = presentation

    def empty(self, title, desc):
        self.empty_status = (title, desc)


class FakeMasterDetailPage:
    def __init__(self, window, title, subtitle, empty_title, empty_desc):
        self.window = window
        self.title = title
        self.toolbar = FakeBox()
        self.header_actions = FakeBox()
        self.detail = FakeDetail()
        self.selected = None
        self.rows = []
        self.after_toolbar = []

    def insert_child_after(self, child, sibling):
        assert sibling is self.toolbar
        self.after_toolbar.append(child)

    def connect_selected(self, handler):
        self.selected = handler

    def show_rows(self, rows, total_count=None):
        self.rows = list(rows)


class FakeListItem:
    def __init__(self, key, title, subtitle, meta, badge, thumbnail):
        self.key = key
        self.title = title
        self.subtitle = subtitle
        self.meta = meta
        self.badge = badge
        self.thumbnail = thumbnail


class Strings:
    @staticmethod
    def new(labels):
        return list(labels)


GTK = NS(Label=FakeLabel, DropDown=FakeDropDown,
         StringList=Strings, Button=FakeButton)


def environment(tmp_path):
    db = Storage(tmp_path / "library.sqlite3")
    for person in ("alice", "bob"):
        db.identity_put(Identity(person, person.capitalize()))
        db.device_usb_binding_declare(
            identity_id=person, device_id=f"reader-{person}",
            label=f"Lecteur {person}",
            usb_fingerprint=f"uuid-{person}-0001",
        )
    db.device_usb_binding_declare(
        identity_id="alice", device_id="reader-alice-2",
        label="Lecteur Alice 2", usb_fingerprint="uuid-alice-0002",
    )
    entries = []
    for owner in ("alice", "bob"):
        data = (owner + " private fixture").encode()
        digest = sha256(data).hexdigest()
        artifact = "sha256:" + digest
        local_path = tmp_path / owner / "story.epub"
        local_path.parent.mkdir()
        local_path.write_bytes(data)
        db.artifact_put({
            "artifact_id": artifact, "sha256": digest, "size": len(data),
            "media_format": "epub", "metadata": {"artifact_kind": "file"},
        })
        placement = db.placement_put({
            "artifact_id": artifact, "path": str(local_path),
            "identity_id": owner, "provider_id": "local",
            "source_item_id": owner, "state": "present", "metadata": {},
        })
        entries.append({
            "key": f"{owner}-book", "identity_id": owner,
            "artifact_id": artifact, "placement_id": placement,
            "title": f"Private {owner} title",
            "author": owner.title(), "local": True,
            "provider_id": "local", "thumbnail_url": None,
        })
    service = NS(current_identity=NS(identity_id="alice"), storage=db,
                 library_entries=lambda **kwargs: (_ for _ in ()).throw(
                     AssertionError("never fetch Library during reader navigation")))
    actions = []
    host = NS(service=service, _library_entries_cache=entries,
              _closed=False, _identity_context_generation=0,
              _add_local_book_actions=lambda detail, item: actions.append(item),
              _readers_rendering=False)
    host._render_readers = lambda: reader_page_controller.render_readers(
        host, Gtk=GTK, BookListItem=FakeListItem,
    )
    host.readers_page = reader_page_controller.build_readers_page(
        host, Gtk=GTK, MasterDetailPage=FakeMasterDetailPage,
    )
    return db, entries, host, actions


def test_builtin_reader_route_and_standard_shell_builder(tmp_path):
    assert "readers" in [page.route for page in Pages().pages()]
    w = NS(plugin_manager=None)
    for route in ("dashboard", "loans", "search", "history", "library",
                  "readers", "recent", "sync", "tools", "settings"):
        setattr(w, f"_build_{route}_page",
                lambda route=route: "reader-page" if route == "readers" else route)
    built = build_standard_page(
        w, "readers", Gtk=NS(Label=lambda **kw: None),
        PluginManagerPage=lambda *args: None,
    )
    assert built == "reader-page"
    assert w.readers_page == "reader-page"


def test_mainwindow_route_delegates_to_page_without_inline_provider_io():
    root = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
    s = (root / "main_window.py").read_text(encoding="utf-8")
    tree = ast.parse(s)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
               and n.name == "MainWindow")
    for name in ("_build_readers_page", "_render_readers"):
        fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                  and n.name == name)
        assert "reader_page_controller." in ast.unparse(fn)
    route = (root / "route_display_controller.py").read_text(encoding="utf-8")
    assert 'route == "readers"' in route
    assert "window._render_readers()" in route


def test_reader_page_is_master_detail_with_selector_and_no_usb_scan(tmp_path):
    db, entries, host, actions = environment(tmp_path)
    page = host.readers_page
    assert isinstance(page, FakeMasterDetailPage)
    assert page.title == "Liseuses"
    assert len(page.toolbar.children) == 4
    assert len(page.after_toolbar) == 1
    host._render_readers()
    assert host._reader_scope_identity == "alice"
    assert host._reader_device_ids == ("reader-alice", "reader-alice-2")
    assert len(page.rows) == 1
    assert page.rows[0].title == "Private alice title"
    assert page.rows[0].meta.startswith("Aucun souhait")
    assert "Présence USB actuelle non confirmée" in host._reader_status_label.label
    assert "Appareil désactivé" in host._reader_status_label.label
    assert actions == []
    assert db.device_placements() == []


def test_selector_restricts_intentions_to_selected_device(tmp_path):
    db, entries, host, actions = environment(tmp_path)
    alice = entries[0]
    db.device_usb_intent_set(
        identity_id="alice", device_id="reader-alice-2",
        artifact_id=alice["artifact_id"], desired_state="keep",
    )
    db.device_usb_intent_set(
        identity_id="alice", device_id="reader-alice-2",
        artifact_id=alice["artifact_id"], desired_state="remove",
    )
    host._render_readers()
    assert host.readers_page.rows[0].meta.startswith("Aucun souhait")
    host._reader_device_dropdown.set_selected(1)
    host._reader_device_dropdown.trigger()
    assert host._reader_selected_device_id == "reader-alice-2"
    assert host.readers_page.rows[0].meta.startswith("À retirer")
    host._reader_filter_dropdown.set_selected(1)
    host._reader_filter_dropdown.trigger()
    assert host.readers_page.rows == []
    host._reader_filter_dropdown.set_selected(2)
    host._reader_filter_dropdown.trigger()
    assert len(host.readers_page.rows) == 1
    assert "à retirer" in host._reader_status_label.label


def test_book_detail_uses_generic_panel_and_exact_current_local_placement(tmp_path):
    db, entries, host, actions = environment(tmp_path)
    data = entries[0]
    binding = db.device_binding_get("reader-alice")
    managed = binding["target_directory"]
    target = managed + "/" + data["artifact_id"].removeprefix("sha256:") + ".epub"
    db.device_placement_put({
        "device_id": "reader-alice", "artifact_id": data["artifact_id"],
        "desired_state": "keep", "observed_state": "present",
        "last_observed_at": 1600000000,
        "target_relative_path": target,
        "metadata": {"intent_identity_id": "alice"},
    })
    host._render_readers()
    row = host.readers_page.rows[0]
    host.readers_page.selected(row)
    shown = host.readers_page.detail.presentation
    assert shown is not None
    assert shown.title == "Private alice title"
    assert actions[0]["identity_id"] == "alice"
    assert actions[0]["placement_id"] == data["placement_id"]
    assert actions[0]["artifact_id"] == data["artifact_id"]
    assert shown.sections
    text = repr(shown)
    assert "Non confirmée" in text
    assert "ancienne observation" in text
    assert "Private bob" not in text
    fields = {field.label: field.value for field in shown.sections[0].fields}
    assert fields["Dernière observation"] == format_recent_timestamp(1600000000, detailed=True)
    assert fields["Chemin réservé"] == target
    assert fields["Format prévu"] == "EPUB"


def test_identity_switch_isolation_and_missing_binding_clears_rows(tmp_path):
    db, entries, host, actions = environment(tmp_path)
    host._render_readers()
    assert len(host.readers_page.rows) == 1
    host.service.current_identity = NS(identity_id="bob")
    host._identity_context_generation += 1
    host._render_readers()
    assert host._reader_scope_identity == "bob"
    assert host._reader_selected_device_id == "reader-bob"
    assert len(host.readers_page.rows) == 1
    assert host.readers_page.rows[0].title == "Private bob title"
    assert "Private alice" not in repr(host.readers_page.rows)
    # An old Alice row cannot activate actions in Bob's detail.
    host.readers_page.selected(NS(key="alice-book"))
    assert actions == []
    host.service.current_identity = None
    host._render_readers()
    assert host.readers_page.rows == []


def test_reader_navigation_does_not_fabricate_physical_presence(tmp_path):
    db, entries, host, actions = environment(tmp_path)
    row = entries[0]
    db.device_placement_put({
        "device_id": "reader-alice", "artifact_id": row["artifact_id"],
        "desired_state": "keep", "observed_state": "present",
        "last_observed_at": 1500000000,
        "metadata": {"intent_identity_id": "alice"},
    })
    host._render_readers()
    summary = host._reader_status_label.label
    assert "non confirmée" in summary
    assert "Présent sur le support" not in summary
    assert "Private bob" not in repr(host.readers_page.rows)


def test_unproven_legacy_intention_refuses_reader_projection_without_leak(tmp_path):
    db, entries, host, actions = environment(tmp_path)
    db.device_placement_put({
        "device_id": "reader-alice", "artifact_id": entries[0]["artifact_id"],
        "desired_state": "keep", "observed_state": "present",
        "metadata": {"intent_identity_id": "bob", "private": "SECRET"},
    })
    host._render_readers()
    assert host.readers_page.rows == []
    assert "SECRET" not in host._reader_status_label.label
    assert actions == []
